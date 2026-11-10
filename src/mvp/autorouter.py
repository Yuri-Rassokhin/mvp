import sys
import os
import argparse
import asyncio
import re
import secrets
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from rich.console import Console
import inspect
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
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
    INTEGRATION_NAMESPACE_MODE,
    MANAGER_OPENAPI_TITLE,
    MESH_PROTOCOL_VERSION,
    METRICS_AUTH_REQUIRED,
    MOCK_DEFAULT_TIMEOUT,
    MODULE_HEADER_NAME,
    OPENAPI_DESCRIPTION,
    OPENAPI_SPEC_VERSION,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
    PROBE_AUTH_REQUIRED,
    SECRET_TOKEN,
    WORKER_BIND_HOST,
    WORKER_PORT_MAX,
    validate_secret_token,
)
from .mock_generator import generate_mock_from_schema
from .endpoints import scan_and_import_endpoints
from .file_tree import cleanup_instance_resources
from .hooks import (
    InstanceLifecycleMeta,
    LoadTracker,
    load_orchestrator_hook,
    pascalize_slug,
    slugify_module_name,
)
from .mesh import find_free_port, get_bound_socket
from .gossip import GossipMesh, GossipPayload, InstanceState

### SETTINGS ###
validate_secret_token()
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=True)
optional_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

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
instance_id = args.instance_id
component_dir = manifest_path.parent


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not secrets.compare_digest(api_key.encode("utf-8"), SECRET_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid API Key")


def _make_conditional_auth_dep(require_auth: bool):
    async def _dep(api_key: Optional[str] = Security(optional_api_key_header)):
        if not require_auth:
            return
        if not api_key or not secrets.compare_digest(
            api_key.encode("utf-8"), SECRET_TOKEN.encode("utf-8")
        ):
            raise HTTPException(status_code=403, detail="Invalid API Key")
    return _dep


verify_probe_auth = _make_conditional_auth_dep(PROBE_AUTH_REQUIRED)
verify_metrics_auth = _make_conditional_auth_dep(METRICS_AUTH_REQUIRED)


# Надежный поиск корня репозитория (где лежит .git) для работы абсолютных импортов из src/
def get_repo_root(path: Path) -> Path:
    curr = path.resolve()
    while curr != curr.parent:
        if (curr / ".git").exists():
            return curr
        curr = curr.parent
    return path.parent.parent.parent.resolve()


def _rewrite_schema_refs(obj: Any, rename_map: Dict[str, str]) -> Any:
    """Рекурсивно переписывает ссылки $ref внутри OpenAPI-фрагмента модуля во избежание коллизий схем."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/"):
                old_name = v.split("/")[-1]
                new_name = rename_map.get(old_name, old_name)
                out[k] = f"#/components/schemas/{new_name}"
            else:
                out[k] = _rewrite_schema_refs(v, rename_map)
        return out
    if isinstance(obj, list):
        return [_rewrite_schema_refs(item, rename_map) for item in obj]
    return obj


def build_deduplicated_integration_openapi(
    registry: Dict[str, InstanceState],
    gateway_server_url: str,
    orchestrator_hook,
    openapi_cfg: Optional[Dict[str, Any]] = None,
    namespace_mode: str = INTEGRATION_NAMESPACE_MODE,
) -> Dict[str, Any]:
    """
    Собирает внешний OpenAPI 3.0.3 контракт (/integration) с абстракцией от инфраструктуры:
      1. Схлопывает (дедуплицирует) все живые реплики одного модуля в единый функциональный модуль.
      2. Исключает коллизии имен путей (/{module_slug}/{ep}), operationId и схем components.schemas.
      3. Не раскрывает внутренние IP/порты реплик — публикует только публичный DNS-адрес шлюза/модуля.
    """
    openapi_cfg = openapi_cfg or {}
    global_schema: Dict[str, Any] = {
        "openapi": openapi_cfg.get("openapi_version", OPENAPI_SPEC_VERSION),
        "info": {
            "title": openapi_cfg.get("title", OPENAPI_TITLE),
            "version": openapi_cfg.get("version", OPENAPI_VERSION),
            "description": openapi_cfg.get("description", OPENAPI_DESCRIPTION),
        },
        "servers": [
            {
                "url": gateway_server_url,
                "description": "Mesh Global Gateway",
            }
        ],
        "paths": {},
        "components": {"schemas": {}},
    }

    # 1. Дедупликация реплик: группируем живые инстансы по каноническому module_slug
    #    и выбираем одного представителя с максимальным набором путей и самым свежим heartbeat.
    representatives: Dict[str, Tuple[str, InstanceState, int]] = {}
    for i_id, state in registry.items():
        if getattr(state, "status", "active") == "dead":
            continue
        contract = state.contract
        if not contract:
            continue

        info = contract.get("info", {})
        mod_title = info.get("title", "Unknown Module")
        mod_slug = info.get("x-module-slug") or slugify_module_name(mod_title)

        if mod_slug not in representatives:
            representatives[mod_slug] = (mod_title, state, 1)
        else:
            prev_title, prev_state, prev_count = representatives[mod_slug]
            prev_paths_len = len(prev_state.contract.get("paths", {}))
            curr_paths_len = len(contract.get("paths", {}))
            if (curr_paths_len, state.heartbeat) > (prev_paths_len, prev_state.heartbeat):
                representatives[mod_slug] = (mod_title, state, prev_count + 1)
            else:
                representatives[mod_slug] = (prev_title, prev_state, prev_count + 1)

    # 2. Подсчитываем частоту коротких путей для режима "on_conflict"
    raw_path_counts: Dict[str, int] = {}
    for mod_slug, (_, rep_state, _) in representatives.items():
        for p_key in rep_state.contract.get("paths", {}).keys():
            raw_path_counts[p_key] = raw_path_counts.get(p_key, 0) + 1

    # Подсчитываем частоту имен схем между разными модулями
    raw_schema_counts: Dict[str, int] = {}
    for mod_slug, (_, rep_state, _) in representatives.items():
        for s_name in rep_state.contract.get("components", {}).get("schemas", {}).keys():
            raw_schema_counts[s_name] = raw_schema_counts.get(s_name, 0) + 1

    # 3. Сборка бесконфликтного OpenAPI 3.0.3 документа
    for mod_slug in sorted(representatives.keys()):
        mod_title, rep_state, _ = representatives[mod_slug]
        contract = rep_state.contract
        pascal_prefix = pascalize_slug(mod_slug)
        safe_op_prefix = mod_slug.replace("-", "_")

        mod_schemas = contract.get("components", {}).get("schemas", {})
        schema_rename_map: Dict[str, str] = {}
        for s_name in mod_schemas.keys():
            if namespace_mode == "always" or raw_schema_counts.get(s_name, 0) > 1:
                schema_rename_map[s_name] = f"{pascal_prefix}_{s_name}"
            else:
                schema_rename_map[s_name] = s_name

        for s_name, s_def in mod_schemas.items():
            target_s_name = schema_rename_map[s_name]
            global_schema["components"]["schemas"][target_s_name] = _rewrite_schema_refs(
                copy.deepcopy(s_def), schema_rename_map
            )

        mod_server_url = orchestrator_hook.resolve_module_server_url(
            module_title=mod_title,
            module_slug=mod_slug,
            fallback_gateway_url=gateway_server_url,
        )

        for path_key, path_item in contract.get("paths", {}).items():
            clean_ep = path_key.lstrip("/")
            use_ns = (
                namespace_mode == "always"
                or (namespace_mode == "on_conflict" and raw_path_counts.get(path_key, 0) > 1)
            )
            exposed_path = f"/{mod_slug}/{clean_ep}" if use_ns else f"/{clean_ep}"

            rewritten_item = _rewrite_schema_refs(copy.deepcopy(path_item), schema_rename_map)

            for method, op in rewritten_item.items():
                if not isinstance(op, dict):
                    continue
                if "tags" not in op:
                    op["tags"] = [mod_title]
                safe_ep = re.sub(r"[^a-zA-Z0-9_]+", "_", clean_ep).strip("_").lower()
                op["operationId"] = f"{safe_op_prefix}__{safe_ep}_{method.lower()}"
                op["x-mvp-module"] = mod_title
                op["x-mvp-module-slug"] = mod_slug
                if mod_server_url:
                    op["servers"] = [{"url": mod_server_url, "description": f"{mod_title} Service"}]

            global_schema["paths"][exposed_path] = rewritten_item

    return global_schema


component_root = str(get_repo_root(manifest_path))

with manifest_path.open("r") as f:
    manifest = yaml.safe_load(f)

component_title = manifest.get("title", manifest.get("name", DEFAULT_COMPONENT_NAME))
component_slug = manifest.get("slug") or slugify_module_name(component_title)
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

    active_tier = DEFAULT_TIER
    is_draining = False
    ready_notified = False
    last_worker_port = port  # отслеживаем последний выданный порт

    lifecycle_meta = InstanceLifecycleMeta(
        instance_id=instance_id,
        module_title=component_title,
        module_slug=component_slug,
        base_url=base_url,
        bind_host=GATEWAY_BIND_HOST,
        port=port,
        active_tier=active_tier,
        manifest_path=str(manifest_path),
        pid=os.getpid(),
    )
    orchestrator_hook = load_orchestrator_hook()
    load_tracker = LoadTracker()

    async def _on_gossip_topology_change(updated_registry: Dict[str, InstanceState]):
        lifecycle_meta.active_tier = active_tier
        await orchestrator_hook.on_mesh_topology_changed(lifecycle_meta, updated_registry)

    mesh = GossipMesh(
        instance_id=instance_id,
        base_url=base_url,
        on_registry_change=_on_gossip_topology_change,
    )

    workers = {
        "prod": {"process": None, "port": None, "active": False, "commit": "HEAD"},
        "dev": {"process": None, "port": None, "active": False, "commit": "HEAD"}
    }
    schema_cache = {"prod": None, "dev": None}
    local_proxies: Dict[str, Any] = {}
    proxy_client = httpx.AsyncClient()
    git_lock = asyncio.Lock()

    def _count_module_active_replicas() -> int:
        cnt = 0
        for state in mesh.registry.values():
            if getattr(state, "status", "active") == "dead":
                continue
            info = (state.contract or {}).get("info", {})
            slug = info.get("x-module-slug") or slugify_module_name(info.get("title", ""))
            if slug == component_slug:
                cnt += 1
        return max(1, cnt) if workers["prod"]["active"] else cnt

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
        global is_draining
        await orchestrator_hook.on_gateway_started(lifecycle_meta)

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
            is_draining = True
            await orchestrator_hook.on_instance_draining(lifecycle_meta, load_tracker=load_tracker)
            await mesh.leave_network()
            await orchestrator_hook.on_instance_stopped(lifecycle_meta, registry=mesh.registry)
            for t in ["prod", "dev"]:
                if workers[t]["process"]:
                    workers[t]["process"].terminate()
            await proxy_client.aclose()
            cleanup_instance_resources(instance_id, component_dir)
            print(f"[{instance_id}] Shutting down Gateway...")

    # Внимание: глобальную зависимость verify_api_key вешаем на бизнес- и админ-маршруты,
    # чтобы пробы балансировщиков (/_sys/health, /_sys/ready) управлялись флагом MVP_PROBE_AUTH_REQUIRED.
    app = FastAPI(
        title=component_title,
        description=component_subtitle,
        lifespan=lifespan,
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
            with load_tracker.track(ep_name) as track_ctx:
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
                    track_ctx.mark_error()
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
                        track_ctx.mark_error()
                        return await fallback(f"{tier.upper()} worker returned HTTP {resp.status_code}")
                    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
                except Exception as e:
                    track_ctx.mark_error()
                    return await fallback(str(e))
        return route_proxy

    for ep_name, ep_cfg in endpoints_config.items():
        if ep_cfg.get("visibility", "public") == "public":
            proxy_fn = make_proxy(ep_name, ep_cfg)
            local_proxies[ep_name] = proxy_fn
            app.post(f"/{ep_name}", name=ep_name, dependencies=[Depends(verify_api_key)])(proxy_fn)
            # Поддерживаем также локальный вызов по каноническому пути /{component_slug}/{ep_name} без сетевого хопа
            app.post(
                f"/{component_slug}/{ep_name}",
                include_in_schema=False,
                dependencies=[Depends(verify_api_key)],
            )(proxy_fn)

    # -----------------------------------------------------------------------
    # СИСТЕМНЫЕ ЭНДПОИНТЫ ПРОБ И АВТОСКЕЙЛИНГА (KEDA / Prometheus / Cloud LB)
    # -----------------------------------------------------------------------
    @app.get("/_sys/health", include_in_schema=False, dependencies=[Depends(verify_probe_auth)])
    async def sys_health():
        return {
            "status": "draining" if is_draining else "ok",
            "instance_id": instance_id,
            "module": component_title,
            "module_slug": component_slug,
            "active_tier": active_tier,
        }

    @app.get("/_sys/ready", include_in_schema=False, dependencies=[Depends(verify_probe_auth)])
    async def sys_ready():
        is_ready = (not is_draining) and (
            active_tier == "mock" or bool(workers.get(active_tier, {}).get("active"))
        )
        payload = {
            "ready": is_ready,
            "draining": is_draining,
            "instance_id": instance_id,
            "module": component_title,
            "module_slug": component_slug,
            "active_tier": active_tier,
        }
        return JSONResponse(status_code=200 if is_ready else 503, content=payload)

    @app.get("/_sys/load", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_load():
        is_ready = bool(workers.get("prod", {}).get("active")) or active_tier == "mock"
        lifecycle_meta.active_tier = active_tier
        return load_tracker.snapshot(
            meta=lifecycle_meta,
            is_ready=is_ready,
            is_draining=is_draining,
            module_active_replicas=_count_module_active_replicas(),
        )

    @app.get(
        "/_sys/metrics",
        include_in_schema=False,
        response_class=PlainTextResponse,
        dependencies=[Depends(verify_metrics_auth)],
    )
    async def sys_metrics():
        is_ready = bool(workers.get("prod", {}).get("active")) or active_tier == "mock"
        lifecycle_meta.active_tier = active_tier
        body = load_tracker.to_prometheus(
            meta=lifecycle_meta,
            is_ready=is_ready,
            is_draining=is_draining,
            module_active_replicas=_count_module_active_replicas(),
        )
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    # -----------------------------------------------------------------------
    # УПРАВЛЕНИЕ ТИРАМИ И СХЕМАМИ
    # -----------------------------------------------------------------------
    @app.post("/_sys/switch", include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def sys_switch(request: Request):
        global active_tier
        data = await request.json()
        target = data.get("tier", DEFAULT_TIER).lower()
        if target in ["prod", "dev", "mock"]:
            active_tier = target
            lifecycle_meta.active_tier = active_tier
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
        global schema_cache, workers, ready_notified
        schema_cache[tier] = await request.json()
        if tier in workers:
            workers[tier]["active"] = True
        if tier == "prod" and not ready_notified:
            ready_notified = True
            mesh._update_self_state(build_local_contract())
            await orchestrator_hook.on_instance_ready(lifecycle_meta, mesh.registry)
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
        schema["info"]["x-module-slug"] = component_slug
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

    # ВНУТРЕННИЙ КОНТУР: /openapi сохраняет порепликовую детализацию (instance_id, base_url каждой реплики)
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
                "module_slug": info.get("x-module-slug") or slugify_module_name(info.get("title", "Unknown Module")),
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

    # ВНЕШНИЙ КОНТУР: /integration дедуплицирует реплики и устраняет коллизии имен по стандарту OpenAPI 3.0.3
    @app.api_route("/integration", methods=["GET", "POST"], dependencies=[Depends(verify_api_key)])
    def get_strict_openapi(request: Request):
        gateway_url = orchestrator_hook.resolve_public_gateway_url(
            str(request.base_url).rstrip("/"), lifecycle_meta
        )
        return build_deduplicated_integration_openapi(
            registry=mesh.registry,
            gateway_server_url=gateway_url,
            orchestrator_hook=orchestrator_hook,
            openapi_cfg=manifest.get("openapi", {}),
            namespace_mode=INTEGRATION_NAMESPACE_MODE,
        )

    def _collect_mesh_candidates(
        raw_path: str, requested_module: Optional[str]
    ) -> List[Tuple[str, InstanceState, str]]:
        """
        Находит все живые реплики в Gossip-реестре, обслуживающие запрошенный путь.
        Поддерживает:
          1. Канонический путь с неймспейсом модуля: /{module_slug}/{ep_name}
          2. Короткий путь /{ep_name} + опциональный заголовок X-MVP-Module: <title или slug>
        Возвращает список кортежей (instance_id, state, target_ep_name).
        """
        clean = raw_path.strip("/")
        if not clean:
            return []

        req_mod_slug = slugify_module_name(requested_module) if requested_module else None
        parts = clean.split("/", 1)

        candidates: List[Tuple[str, InstanceState, str]] = []

        # Попытка A: трактуем первый сегмент как module_slug (/{module_slug}/{ep_name})
        if len(parts) == 2 and not req_mod_slug:
            cand_slug, cand_ep = parts[0].lower(), parts[1]
            for i_id, state in mesh.registry.items():
                if getattr(state, "status", "active") == "dead" or not state.contract:
                    continue
                info = state.contract.get("info", {})
                node_slug = info.get("x-module-slug") or slugify_module_name(info.get("title", ""))
                if node_slug == cand_slug and f"/{cand_ep}" in state.contract.get("paths", {}):
                    candidates.append((i_id, state, cand_ep))
            if candidates:
                return candidates

        # Попытка B: трактуем путь целиком как /{ep_name} (с учетом X-MVP-Module, если передан)
        for i_id, state in mesh.registry.items():
            if getattr(state, "status", "active") == "dead" or not state.contract:
                continue
            info = state.contract.get("info", {})
            node_title = info.get("title", "")
            node_slug = info.get("x-module-slug") or slugify_module_name(node_title)
            if req_mod_slug and node_slug != req_mod_slug and node_title.lower() != requested_module.lower():
                continue
            if f"/{clean}" in state.contract.get("paths", {}):
                candidates.append((i_id, state, clean))

        return candidates

    # 4. УНИВЕРСАЛЬНЫЙ MESH-ПРОКСИ (ТОТ САМЫЙ GATEWAY С БАЛАНСИРОВКОЙ РЕПЛИК И FAILOVER)
    # ВАЖНО: Этот декоратор с {path:path} должен быть объявлен ПОСЛЕДНИМ из всех маршрутов,
    # так как он перехватывает все пути, которые не были обработаны локально (make_proxy).
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False, dependencies=[Depends(verify_api_key)])
    async def global_mesh_proxy(request: Request, response: Response, path: str):
        # Игнорируем внутренние системные вызовы фреймворка
        if path.startswith("_sys") or path in ["contract", "network", "openapi", "integration", "syslog", "syslog-stream"]:
            raise HTTPException(status_code=404, detail="Not found")

        requested_module = request.headers.get(MODULE_HEADER_NAME)
        candidates = _collect_mesh_candidates(path, requested_module=requested_module)

        if not candidates:
            raise HTTPException(status_code=404, detail=f"Path /{path} not found in active mesh network")

        # Выбираем стартовую реплику через хук балансировки (Round-Robin / Random),
        # а остальные реплики держим в очереди автоматического failover!
        lb_key = f"{requested_module or ''}:{path}"
        primary_pair = orchestrator_hook.select_replica(
            [(i_id, (state, ep_name)) for i_id, state, ep_name in candidates],
            key=lb_key,
        )
        ordered_targets = [primary_pair] + [
            (i_id, (state, ep_name))
            for i_id, state, ep_name in candidates
            if i_id != primary_pair[0]
        ]

        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}

        last_error: Optional[Exception] = None
        last_base_url: str = ""

        with load_tracker.track(f"mesh:{path}") as track_ctx:
            for chosen_id, (chosen_state, target_ep) in ordered_targets:
                # Если балансировщик выбрал текущий узел — исполняем локально без лишнего HTTP-хопа
                if chosen_id == instance_id and target_ep in local_proxies:
                    return await local_proxies[target_ep](request, response)

                last_base_url = chosen_state.base_url
                try:
                    resp = await proxy_client.request(
                        method=request.method,
                        url=f"{chosen_state.base_url}/{target_ep}",
                        content=body,
                        headers=headers,
                        params=request.query_params
                    )
                    if resp.status_code >= 500:
                        track_ctx.mark_error()
                    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
                except httpx.RequestError as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    track_ctx.mark_error()
                    raise HTTPException(
                        status_code=502,
                        detail=f"Mesh Gateway Error (Target: {last_base_url}): {str(exc)}",
                    )

            track_ctx.mark_error()
            raise HTTPException(
                status_code=502,
                detail=f"Mesh Gateway Error (All {len(ordered_targets)} replica(s) unreachable, last={last_base_url}): {last_error}",
            )

    if __name__ == "__main__":
        config = uvicorn.Config(app, loop="asyncio")
        server = uvicorn.Server(config)
        asyncio.run(server.serve(sockets=[gateway_sock]))
