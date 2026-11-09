import sys
import os
import argparse
import asyncio
import secrets
import signal
import socket
import subprocess
from pathlib import Path
import yaml
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from rich.console import Console
import inspect
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import uvicorn
import copy

from .config import (
    ADVERTISE_HOST,
    API_KEY_HEADER_NAME,
    AUTOROUTER_LIFESPAN_DEV_SLEEP_SEC,
    AUTOROUTER_SCHEMA_PUSH_ATTEMPTS,
    AUTOROUTER_SCHEMA_PUSH_SLEEP_SEC,
    AUTOROUTER_SCHEMA_PUSH_TIMEOUT_SEC,
    AUTOROUTER_UPDATE_DEV_SLEEP_SEC,
    CONSOLE_WIDTH,
    CORS_ORIGINS,
    DEFAULT_COMPONENT_NAME,
    DEFAULT_DEV_BRANCH,
    DEFAULT_PROD_BRANCH,
    DEFAULT_TIER,
    GATEWAY_BIND_HOST,
    GIT_REMOTE,
    MANAGER_OPENAPI_TITLE,
    MESH_PROTOCOL_VERSION,
    MOCK_DEFAULT_TIMEOUT,
    OPENAPI_DESCRIPTION,
    OPENAPI_SPEC_VERSION,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
    SECRET_TOKEN,
    WORKER_BIND_HOST,
    WORKER_PORT_MAX,
    validate_secret_token,
)
from .mock_generator import generate_mock_from_schema
from .endpoints import scan_and_import_endpoints
from .file_tree import cleanup_instance_resources
from .mesh import find_free_port, get_bound_socket
from .gossip import GossipMesh, GossipPayload

### SETTINGS ###
validate_secret_token()
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=True)

console = Console(force_terminal=True, width=CONSOLE_WIDTH)

parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument("manifest_path")
parser.add_argument("instance_id")
parser.add_argument("--worker", action="store_true")
parser.add_argument("--worker-port", type=int)
parser.add_argument("--worker-fd", type=int)
parser.add_argument("--gateway-port", type=int)
parser.add_argument("--tier", type=str, default=DEFAULT_TIER) # Указание тира для воркера
args, unknown = parser.parse_known_args()

manifest_path = Path(args.manifest_path).resolve()
instance_id = args.instance_id # или твой вариант получения instance_id
component_dir = manifest_path.parent


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not secrets.compare_digest(api_key.encode("utf-8"), SECRET_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid API Key")

# Надежный поиск корня репозитория (где лежит .git) для работы абсолютных импортов из src/
def get_repo_root(path: Path) -> Path:
    curr = path.resolve()
    while curr != curr.parent:
        if (curr / ".git").exists():
            return curr
        curr = curr.parent
    return path.parent.parent.parent.resolve()

component_root = str(get_repo_root(manifest_path))

with manifest_path.open("r") as f: manifest = yaml.safe_load(f)

component_title = manifest.get("title", manifest.get("name", DEFAULT_COMPONENT_NAME))
component_subtitle = manifest.get("subtitle", manifest.get("description", ""))
start_funcs = manifest.get("start", [])

source_config = manifest.get("source", {})
git_remote = source_config.get("remote", GIT_REMOTE)
prod_branch = source_config.get("branch", DEFAULT_PROD_BRANCH)
prod_commit = source_config.get("commit")

dev_config = source_config.get("dev", {})
if isinstance(dev_config, str):
    dev_branch = dev_config
elif isinstance(dev_config, dict):
    dev_branch = dev_config.get("branch", DEFAULT_DEV_BRANCH)
else:
    dev_branch = DEFAULT_DEV_BRANCH

cors_origins = manifest.get("cors_origins", CORS_ORIGINS)
raw_endpoints = manifest.get("endpoints", {})

endpoints_config = {ep: {"visibility": "public"} for ep in raw_endpoints} if isinstance(raw_endpoints, list) else raw_endpoints
public_funcs = {name for name, cfg in endpoints_config.items() if cfg.get("visibility", "public") == "public"}

if args.worker:
    @asynccontextmanager
    async def worker_lifespan(app: FastAPI):
        async def _push_schema():
            # Повторяем попытки AUTOROUTER_SCHEMA_PUSH_ATTEMPTS раз, пока Gateway не начнет принимать HTTP
            for attempt in range(AUTOROUTER_SCHEMA_PUSH_ATTEMPTS):
                await asyncio.sleep(AUTOROUTER_SCHEMA_PUSH_SLEEP_SEC)
                try:
                    async with httpx.AsyncClient(timeout=AUTOROUTER_SCHEMA_PUSH_TIMEOUT_SEC) as client:
                        resp = await client.post(
                            f"http://{WORKER_BIND_HOST}:{args.gateway_port}/_sys/schema?tier={args.tier}",
                            json=app.openapi(),
                            headers={API_KEY_HEADER_NAME: SECRET_TOKEN}
                        )
                        if resp.status_code == 200:
                            print(f"INFO: Worker {args.tier.upper()} successfully pushed schema to Gateway.")
                            break
                except Exception as e:
                    if attempt == AUTOROUTER_SCHEMA_PUSH_ATTEMPTS - 1:
                        print(f"WARN: Worker {args.tier.upper()} failed to push schema after {AUTOROUTER_SCHEMA_PUSH_ATTEMPTS} attempts: {repr(e)}")

        asyncio.create_task(_push_schema())
        yield

    app = FastAPI(
        title=f"{component_title} ({args.tier.upper()})",
        lifespan=worker_lifespan,
        dependencies=[Depends(verify_api_key)],
    )
    modules = scan_and_import_endpoints(component_dir, endpoints_config, start_funcs, app, component_root)

    if __name__ == "__main__":
        if args.worker_fd is not None:
            uvicorn.run(app, fd=args.worker_fd)
        else:
            uvicorn.run(app, host=WORKER_BIND_HOST, port=args.worker_port)

else:
    gateway_sock, port = get_bound_socket(host=GATEWAY_BIND_HOST)
    base_url = f"http://{ADVERTISE_HOST}:{port}"
    mesh = GossipMesh(instance_id=instance_id, base_url=base_url)

    # --- СОСТОЯНИЕ ШЛЮЗА ---
    active_tier = DEFAULT_TIER
    last_worker_port = port  # отслеживаем последний выданный порт

    workers = {
        "prod": {"process": None, "port": None, "active": False, "commit": "HEAD"},
        "dev": {"process": None, "port": None, "active": False, "commit": "HEAD"}
    }
    schema_cache = {"prod": None, "dev": None}
    proxy_client = httpx.AsyncClient()
    git_lock = asyncio.Lock()

    def spawn_worker(tier=DEFAULT_TIER):
        global workers, last_worker_port

        next_start = last_worker_port + 1 if last_worker_port < WORKER_PORT_MAX else port + 1
        worker_sock, worker_port = get_bound_socket(host=WORKER_BIND_HOST, start=next_start)
        last_worker_port = worker_port

        worker_fd = worker_sock.fileno()
        worker_sock.set_inheritable(True)

        cmd = [sys.executable, "-u", "-m", "mvp.autorouter", str(manifest_path), instance_id,
               "--worker", "--worker-port", str(worker_port), "--worker-fd", str(worker_fd),
               "--gateway-port", str(port), "--tier", tier]

        proc = subprocess.Popen(cmd, pass_fds=(worker_fd,), stdout=sys.stdout, stderr=sys.stderr, env=os.environ)
        worker_sock.close()

        workers[tier]["process"] = proc
        workers[tier]["port"] = worker_port
        workers[tier]["active"] = False


    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1. Запускаем Dev (выкачиваем ветку dev_branch)
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "fetch", "--all"], cwd=str(component_dir))
                subprocess.run(["git", "checkout", dev_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", git_remote, dev_branch], cwd=str(component_dir))

        spawn_worker(tier="dev")

        # Даем Dev-воркеру время на инициализацию и чтение файлов в память
        await asyncio.sleep(AUTOROUTER_LIFESPAN_DEV_SLEEP_SEC)

        # 2. Запускаем Prod и оставляем файловую систему в состоянии Prod
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", git_remote, prod_branch], cwd=str(component_dir))
                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))
        spawn_worker(tier="prod")

        asyncio.create_task(mesh.gossip_loop(get_local_contract_func=build_local_contract))
        asyncio.create_task(mesh.reaper_loop())
        try:
            yield
        finally:
            for t in ["prod", "dev"]:
                if workers[t]["process"]:
                    workers[t]["process"].terminate()
            await mesh.leave_network()
            await proxy_client.aclose()
            cleanup_instance_resources(instance_id, component_dir)
            print(f"[{instance_id}] Shutting down Gateway...")

    app = FastAPI(
        title=component_title,
        description=component_subtitle,
        lifespan=lifespan,
        dependencies=[Depends(verify_api_key)],
    )
    app.add_middleware(CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def resolve_mock(ep_name, mock_cfg):
        if "value" in mock_cfg: return mock_cfg["value"]
        if "path" in mock_cfg:
            mock_path = component_dir / mock_cfg["path"]
            try:
                with open(mock_path, "r") as f:
                    if mock_path.suffix in [".yaml", ".yml"]: return yaml.safe_load(f)
                    return json.load(f)
            except Exception as ex:
                return {"error": f"Failed to load mock from {mock_path}: {ex}"}

        if mock_cfg.get("type") == "auto":
            # Берем схему ТОЛЬКО у prod-воркера!
            openapi = schema_cache.get("prod")

            if not openapi:
                return {"mock_status": "error", "notice": "No PROD schema available to generate mocks. Ensure prod worker is running."}

            paths = openapi.get("paths", {})
            ep_path = f"/{ep_name}"

            if ep_path in paths and "post" in paths[ep_path]:
                responses = paths[ep_path]["post"].get("responses", {})

                # Ищем схему успешного ответа (200)
                if "200" in responses:
                    content = responses["200"].get("content", {})
                    if "application/json" in content:
                        response_schema = content["application/json"].get("schema", {})
                        components_schemas = openapi.get("components", {}).get("schemas", {})

                        return generate_mock_from_schema(response_schema, components_schemas)

            return {"mock_status": "auto-generated", "notice": "No Pydantic response model found for this endpoint"}

        return {}

    def make_proxy(ep_name, cfg):
        mock_cfg = cfg.get("mock")
        raw_timeout = mock_cfg.get("timeout", MOCK_DEFAULT_TIMEOUT) if mock_cfg else MOCK_DEFAULT_TIMEOUT
        httpx_timeout = None if raw_timeout == 0 else float(raw_timeout)

        async def route_proxy(request: Request, response: Response):
            # 1. Извлекаем целевой тир из заголовка или берем активный по умолчанию
            tier = request.headers.get("x-mvp-tier", active_tier).lower()
            if tier not in ["prod", "dev", "mock"]:
                tier = "mock"

            async def fallback(err_msg):
                console.print(f"[yellow]WARN: [MOCK TIER] /{ep_name} fallback triggered for tier '{tier}': {err_msg}[/yellow]")
                response.headers["X-MVP-Mock-Fallback"] = "true"
                response.headers["X-MVP-Original-Error"] = str(err_msg)
                return resolve_mock(ep_name, mock_cfg) if mock_cfg else {"error": err_msg}

            if tier == "mock":
                return await fallback("Mock tier explicitly requested")

            # 2. Проверяем, жив ли целевой воркер
            target_worker = workers.get(tier)
            if not target_worker or not target_worker["port"] or not target_worker["active"]:
                return await fallback(f"Target tier '{tier}' is not active or currently updating")

            body = await request.body()
            headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}

            try:
                # 3. Маршрутизируем запрос
                resp = await proxy_client.request(
                    method=request.method,
                    url=f"http://{WORKER_BIND_HOST}:{target_worker['port']}/{ep_name}",
                    content=body, headers=headers, timeout=httpx_timeout
                )
                if resp.status_code >= 500:
                    return await fallback(f"{tier.upper()} worker returned HTTP {resp.status_code}")
                return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
            except Exception as e:
                return await fallback(str(e))
        return route_proxy

    for ep_name, ep_cfg in endpoints_config.items():
        if ep_cfg.get("visibility", "public") == "public":
            app.post(f"/{ep_name}", name=ep_name, dependencies=[Depends(verify_api_key)])(make_proxy(ep_name, ep_cfg))

    @app.post("/_sys/switch", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_switch(request: Request):
        global active_tier
        data = await request.json()
        target = data.get("tier", DEFAULT_TIER).lower()
        if target in ["prod", "dev", "mock"]:
            active_tier = target
        return {"status": f"Active tier switched to {active_tier}"}

    @app.post("/_sys/update_dev", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_update_dev():
        global workers
        workers["dev"]["active"] = False
        if workers["dev"]["process"]:
            workers["dev"]["process"].terminate()
            workers["dev"]["process"].wait()

        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "fetch", "--all"], cwd=str(component_dir))
                subprocess.run(["git", "checkout", dev_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", git_remote, dev_branch], cwd=str(component_dir))

        spawn_worker(tier="dev")
        await asyncio.sleep(AUTOROUTER_UPDATE_DEV_SLEEP_SEC)

        # Обязательно возвращаем ФС обратно на prod, чтобы шлюз не читал чужие моки
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))

        return {"status": f"dev worker updated from {dev_branch}"}

    @app.post("/_sys/update_prod", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_update_prod():
        global workers
        workers["prod"]["active"] = False
        if workers["prod"]["process"]:
            workers["prod"]["process"].terminate()
            workers["prod"]["process"].wait()

        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "fetch", "--all"], cwd=str(component_dir))
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", git_remote, prod_branch], cwd=str(component_dir))
                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))

        spawn_worker(tier="prod")
        return {"status": f"prod worker updated from {prod_branch}"}

    @app.post("/_sys/schema", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_schema(request: Request, tier: str = DEFAULT_TIER):
        global schema_cache, workers
        schema_cache[tier] = await request.json()
        if tier in workers:
            workers[tier]["active"] = True
        return {"status": f"schema received for {tier}"}

    def build_local_contract():
        # Берем схему активного тира для отображения во внешней Gossip-сети
        schema = copy.deepcopy(schema_cache.get(active_tier))

        # Если мы в mock, одалживаем схему ТОЛЬКО у prod
        if not schema and active_tier == "mock":
            schema = copy.deepcopy(schema_cache.get("prod"))

        if not schema:
            schema = copy.deepcopy(app.openapi())

        if "info" not in schema: schema["info"] = {}
        schema["info"]["title"] = component_title
        schema["info"]["description"] = component_subtitle
        schema["info"]["x-contract-path"] = str(manifest_path)
        schema["info"]["x-active-tier"] = active_tier
        schema["info"].pop("version", None)

        filtered_paths = {}
        for path, path_item in schema.get("paths", {}).items():
            if path.lstrip("/") in public_funcs:
                filtered_paths[path] = path_item
        schema["paths"] = filtered_paths
        return schema

    @app.post("/contract", dependencies=[Depends(verify_api_key)])
    async def intro_manifest(response: Response, request: Request):
        await request.body()
        response.headers["X-Mesh-Version"] = MESH_PROTOCOL_VERSION
        return build_local_contract()

    @app.post("/_gossip", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def receive_gossip(payload: GossipPayload, request: Request):
        if request.headers.get("x-mesh-version") != MESH_PROTOCOL_VERSION:
            raise HTTPException(status_code=426, detail="Incompatible mesh protocol version")
        mesh.merge_registry(payload.registry)
        return {"status": "ok"}

    @app.post("/syslog", response_class=PlainTextResponse, dependencies=[Depends(verify_api_key)])
    def get_log():
        result = subprocess.run(["mvp", "log", instance_id], capture_output=True, text=True)
        return result.stdout

    @app.post("/syslog-stream", dependencies=[Depends(verify_api_key)])
    def stream_log():
        process = subprocess.Popen(["mvp", "log", instance_id, "-f"], stdout=subprocess.PIPE)
        def event_stream():
            for line in iter(process.stdout.readline, b''): yield line.decode("utf-8")
        return StreamingResponse(event_stream(), media_type="text/plain")

    @app.api_route("/network", methods=["GET", "POST"], include_in_schema=False, dependencies=[Depends(verify_api_key)])
    def get_global_network():
        return {i_id: state.model_dump() for i_id, state in mesh.registry.items()}

    @app.api_route("/openapi", methods=["GET", "POST"], dependencies=[Depends(verify_api_key)])
    def get_global_openapi():
        global_schema = {"title": MANAGER_OPENAPI_TITLE, "modules": [], "components": {"schemas": {}}}
        for i_id, state in mesh.registry.items():
            if getattr(state, "status", "active") == "dead":
                continue

            info = state.contract.get("info", {})
            module_entry = {
                "instance_id": i_id,
                "title": info.get("title", "Unknown Module"),
                "subtitle": info.get("description", ""),
                "contract_path": info.get("x-contract-path", ""),
                "active_tier": info.get("x-active-tier", DEFAULT_TIER),
                "base_url": state.base_url,
                "endpoints": state.contract.get("paths", {})
            }
            global_schema["modules"].append(module_entry)
            for schema_name, schema_def in state.contract.get("components", {}).get("schemas", {}).items():
                global_schema["components"]["schemas"][schema_name] = schema_def
        return global_schema

    @app.api_route("/integration", methods=["GET", "POST"], dependencies=[Depends(verify_api_key)])
    def get_strict_openapi(request: Request):
        openapi_cfg = manifest.get("openapi", {})
        # 1. Базовый строгий каркас OpenAPI
        global_schema = {
            "openapi": openapi_cfg.get("openapi_version", OPENAPI_SPEC_VERSION),
            "info": {
                "title": openapi_cfg.get("title", OPENAPI_TITLE),
                "version": openapi_cfg.get("version", OPENAPI_VERSION),
                "description": openapi_cfg.get("description", OPENAPI_DESCRIPTION)
            },
            "servers": [
                {
                    # Узел динамически указывает сам себя как глобальный Gateway
                    "url": str(request.base_url).rstrip("/"),
                    "description": "Mesh Global Gateway"
                }
            ],
            "paths": {},
            "components": {
                "schemas": {}
            }
        }

        for i_id, state in mesh.registry.items():
            if getattr(state, "status", "active") == "dead":
                continue

            contract = state.contract
            if not contract: continue

            node_title = contract.get("info", {}).get("title", "Unknown Module")

            # 2. Сбор и маппинг путей
            for path_key, path_item in contract.get("paths", {}).items():
                new_path_item = copy.deepcopy(path_item)

                # Добавляем теги для красивой группировки эндпоинтов в UI Microsoft
                for method, op in new_path_item.items():
                    if isinstance(op, dict) and "tags" not in op:
                        op["tags"] = [node_title]

                global_schema["paths"][path_key] = new_path_item

            # 3. Сбор схем данных (Input/Output Pydantic модели)
            schemas = contract.get("components", {}).get("schemas", {})
            for s_name, s_def in schemas.items():
                # Т.к. Pydantic обычно дает уникальные имена моделям (напр. EthicsInput),
                # мы можем безопасно сливать их в один общий словарь компонентов.
                global_schema["components"]["schemas"][s_name] = copy.deepcopy(s_def)

        return global_schema

    # 4. УНИВЕРСАЛЬНЫЙ MESH-ПРОКСИ (ТОТ САМЫЙ GATEWAY)
    # ВАЖНО: Этот декоратор с {path:path} должен быть объявлен ПОСЛЕДНИМ из всех маршрутов,
    # так как он перехватывает все пути, которые не были обработаны локально (make_proxy).
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def global_mesh_proxy(request: Request, path: str):
        # Игнорируем внутренние системные вызовы фреймворка
        if path.startswith("_sys") or path in ["contract", "network", "openapi", "integration", "syslog", "syslog-stream"]:
            raise HTTPException(status_code=404, detail="Not found")

        target_base_url = None

        # Ищем в Gossip-сети узел, который владеет запрошенным путем
        for i_id, state in mesh.registry.items():
            if getattr(state, "status", "active") == "dead": continue
            if not state.contract: continue

            # Если запрошенный путь числится в контракте чужого узла
            if f"/{path}" in state.contract.get("paths", {}):
                target_base_url = state.base_url
                break

        if not target_base_url:
            raise HTTPException(status_code=404, detail=f"Path /{path} not found in active mesh network")

        # Формируем прокси-запрос
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}

        try:
            resp = await proxy_client.request(
                method=request.method,
                url=f"{target_base_url}/{path}",
                content=body,
                headers=headers,
                params=request.query_params
            )
            return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Mesh Gateway Error (Target: {target_base_url}): {str(e)}")

    if __name__ == "__main__":
        config = uvicorn.Config(app, loop="asyncio")
        server = uvicorn.Server(config)
        asyncio.run(server.serve(sockets=[gateway_sock]))
