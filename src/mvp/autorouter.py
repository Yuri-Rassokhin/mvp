import sys
import copy
import asyncio
import subprocess
import argparse
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Response, Request, HTTPException
from rich.console import Console
import os
import inspect
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
import uvicorn
import httpx

from .endpoints import scan_and_import_endpoints
from .mesh import find_free_port, get_bound_socket
from .gossip import GossipMesh, GossipPayload, MESH_PROTOCOL_VERSION

### SETTINGS ###
MOCK_DEFAULT_TIMEOUT = 0.0

console = Console(force_terminal=True, width=10000)

parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument("manifest_path")
parser.add_argument("instance_id")
parser.add_argument("--worker", action="store_true")
parser.add_argument("--worker-port", type=int)
parser.add_argument("--worker-fd", type=int)
parser.add_argument("--gateway-port", type=int)
parser.add_argument("--tier", type=str, default="prod") # Указание тира для воркера
args, unknown = parser.parse_known_args()

manifest_path = Path(args.manifest_path).resolve()
instance_id = instance_id = args.instance_id # или твой вариант получения instance_id
component_dir = manifest_path.parent

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

component_title = manifest.get("title", manifest.get("name", "unknown-component"))
component_subtitle = manifest.get("subtitle", manifest.get("description", ""))
start_funcs = manifest.get("start", [])

source_config = manifest.get("source", {})
prod_branch = source_config.get("branch", "main")
prod_commit = source_config.get("commit")

dev_config = source_config.get("dev", {})
if isinstance(dev_config, str):
    dev_branch = dev_config
elif isinstance(dev_config, dict):
    dev_branch = dev_config.get("branch", "main")
else:
    dev_branch = "main"

raw_endpoints = manifest.get("endpoints", {})

endpoints_config = {ep: {"visibility": "public"} for ep in raw_endpoints} if isinstance(raw_endpoints, list) else raw_endpoints
public_funcs = {name for name, cfg in endpoints_config.items() if cfg.get("visibility", "public") == "public"}

if args.worker:
    @asynccontextmanager
    async def worker_lifespan(app: FastAPI):
        async def _push_schema():
            await asyncio.sleep(0.5)
            try:
                async with httpx.AsyncClient() as client:
                    # Воркер сообщает шлюзу свой тир при передаче схемы
                    await client.post(f"http://127.0.0.1:{args.gateway_port}/_sys/schema?tier={args.tier}", json=app.openapi())
            except Exception as e:
                print(f"WARN: Worker failed to push schema to gateway: {e}")
        asyncio.create_task(_push_schema())
        yield

    app = FastAPI(title=f"{component_title} ({args.tier.upper()})", lifespan=worker_lifespan)
    modules = scan_and_import_endpoints(component_dir, endpoints_config, start_funcs, app, component_root)
    
    if __name__ == "__main__":
        if args.worker_fd is not None:
            uvicorn.run(app, fd=args.worker_fd)
        else:
            uvicorn.run(app, host="127.0.0.1", port=args.worker_port)

else:
    gateway_sock, port = get_bound_socket(host="0.0.0.0")
    base_url = f"http://127.0.0.1:{port}"
    mesh = GossipMesh(instance_id=instance_id, base_url=base_url)
    
    # --- СОСТОЯНИЕ ШЛЮЗА ---
    active_tier = "prod"
    workers = {
        "prod": {"process": None, "port": None, "active": False, "commit": "HEAD"},
        "dev": {"process": None, "port": None, "active": False, "commit": "HEAD"}
    }
    schema_cache = {"prod": None, "dev": None}
    proxy_client = httpx.AsyncClient()
    git_lock = asyncio.Lock()
    
    def spawn_worker(tier="prod"):
        global workers
        worker_sock, worker_port = get_bound_socket(host="127.0.0.1", start=port + 1)
        worker_fd = worker_sock.fileno()
        worker_sock.set_inheritable(True)
        
        cmd = [sys.executable, "-u", "-m", "mvp.autorouter", str(manifest_path), instance_id, 
               "--worker", "--worker-port", str(worker_port), "--worker-fd", str(worker_fd), 
               "--gateway-port", str(port), "--tier", tier]
               
        proc = subprocess.Popen(cmd, pass_fds=(worker_fd,), stdout=sys.stdout, stderr=sys.stderr)
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
                subprocess.run(["git", "pull", "origin", dev_branch], cwd=str(component_dir))
        
        spawn_worker(tier="dev")
        
        # ДОБАВЛЕНО: Ждем, пока dev-воркер не пришлет схему (значит он полностью загрузил код в память)
        # Опрашиваем кэш с таймаутом до 15 секунд
        for _ in range(150):
            if schema_cache.get("dev") is not None:
                break
            await asyncio.sleep(0.1)
        
        # 2. Запускаем Prod и оставляем файловую систему в состоянии Prod
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", "origin", prod_branch], cwd=str(component_dir))
                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))
        spawn_worker(tier="prod")

        asyncio.create_task(mesh.gossip_loop(get_local_contract_func=build_local_contract))
        asyncio.create_task(mesh.reaper_loop())
        yield
        for t in ["prod", "dev"]:
            if workers[t]["process"]:
                workers[t]["process"].terminate()
        await mesh.leave_network()
        await proxy_client.aclose()
        print(f"[{instance_id}] Shutting down Gateway...")

    app = FastAPI(title=component_title, description=component_subtitle, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

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
            return {"mock_status": "auto-generated", "notice": "Automatic fallback triggered by MVP"}
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
                    url=f"http://127.0.0.1:{target_worker['port']}/{ep_name}",
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
            app.post(f"/{ep_name}", name=ep_name)(make_proxy(ep_name, ep_cfg))

    @app.post("/_sys/switch", include_in_schema=False)
    async def sys_switch(request: Request):
        global active_tier
        data = await request.json()
        target = data.get("tier", "prod").lower()
        if target in ["prod", "dev", "mock"]:
            active_tier = target
        return {"status": f"Active tier switched to {active_tier}"}

    @app.post("/_sys/update_dev", include_in_schema=False)
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
                subprocess.run(["git", "pull", "origin", dev_branch], cwd=str(component_dir))
        
        spawn_worker(tier="dev")
        await asyncio.sleep(1.0)
        
        # Обязательно возвращаем ФС обратно на prod, чтобы шлюз не читал чужие моки
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))
                    
        return {"status": f"dev worker updated from {dev_branch}"}

    @app.post("/_sys/update_prod", include_in_schema=False)
    async def sys_update_prod():
        global workers, prod_branch, prod_commit
        workers["prod"]["active"] = False
        if workers["prod"]["process"]:
            workers["prod"]["process"].terminate()
            workers["prod"]["process"].wait()
            
        async with git_lock:
            if (component_dir / ".git").exists():
                subprocess.run(["git", "fetch", "--all"], cwd=str(component_dir))
                subprocess.run(["git", "checkout", prod_branch], cwd=str(component_dir))
                subprocess.run(["git", "pull", "origin", prod_branch], cwd=str(component_dir))
                
                # Истинный GitOps: перечитываем контракт с диска после пулла
                try:
                    with manifest_path.open("r") as f:
                        fresh_manifest = yaml.safe_load(f)
                    fresh_source = fresh_manifest.get("source", {})
                    prod_branch = fresh_source.get("branch", "main")
                    prod_commit = fresh_source.get("commit")
                except Exception as e:
                    print(f"WARN: Failed to re-read manifest: {e}")

                if prod_commit:
                    subprocess.run(["git", "checkout", prod_commit], cwd=str(component_dir))
                    workers["prod"]["commit"] = prod_commit
                else:
                    workers["prod"]["commit"] = "HEAD"
                    
        spawn_worker(tier="prod")
        return {"status": f"prod worker updated to {prod_commit or 'HEAD'}"}

    @app.post("/_sys/schema", include_in_schema=False)
    async def sys_schema(request: Request, tier: str = "prod"):
        global schema_cache, workers
        schema_cache[tier] = await request.json()
        if tier in workers:
            workers[tier]["active"] = True
        return {"status": f"schema received for {tier}"}

    def build_local_contract():
        # Берем схему активного тира для отображения во внешней Gossip-сети
        schema = copy.deepcopy(schema_cache.get(active_tier))
        if not schema: schema = copy.deepcopy(app.openapi())
        
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

    @app.post("/contract")
    async def intro_manifest(response: Response, request: Request):
        await request.body() 
        response.headers["X-Mesh-Version"] = MESH_PROTOCOL_VERSION
        return build_local_contract()

    @app.post("/_gossip", include_in_schema=False)
    async def receive_gossip(payload: GossipPayload, request: Request):
        if request.headers.get("x-mesh-version") != MESH_PROTOCOL_VERSION:
            raise HTTPException(status_code=426, detail="Incompatible mesh protocol version")
        mesh.merge_registry(payload.registry)
        return {"status": "ok"}

    @app.post("/syslog", response_class=PlainTextResponse)
    def get_log():
        result = subprocess.run(["mvp", "log", instance_id], capture_output=True, text=True)
        return result.stdout

    @app.post("/syslog-stream")
    def stream_log():
        process = subprocess.Popen(["mvp", "log", instance_id, "-f"], stdout=subprocess.PIPE)
        def event_stream():
            for line in iter(process.stdout.readline, b''): yield line.decode("utf-8")
        return StreamingResponse(event_stream(), media_type="text/plain")

    @app.api_route("/network", methods=["GET", "POST"], include_in_schema=False)
    def get_global_network():
        return {i_id: state.model_dump() for i_id, state in mesh.registry.items()}

    @app.api_route("/openapi", methods=["GET", "POST"])
    def get_global_openapi():
        global_schema = {"title": "MVP Framework Manager", "modules": [], "components": {"schemas": {}}}
        for i_id, state in mesh.registry.items():
            if getattr(state, "status", "active") == "dead":
                continue

            info = state.contract.get("info", {})
            module_entry = {
                "instance_id": i_id, 
                "title": info.get("title", "Unknown Module"),
                "subtitle": info.get("description", ""), 
                "contract_path": info.get("x-contract-path", ""),
                "active_tier": info.get("x-active-tier", "prod"),
                "base_url": state.base_url,
                "endpoints": state.contract.get("paths", {})
            }
            global_schema["modules"].append(module_entry)
            for schema_name, schema_def in state.contract.get("components", {}).get("schemas", {}).items():
                global_schema["components"]["schemas"][schema_name] = schema_def
        return global_schema

    if __name__ == "__main__":
        config = uvicorn.Config(app, loop="asyncio")
        server = uvicorn.Server(config)
        asyncio.run(server.serve(sockets=[gateway_sock]))
