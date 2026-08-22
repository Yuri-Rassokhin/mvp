import sys
import copy
import asyncio
import subprocess
import argparse
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Response, Request, HTTPException

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
from .mesh import find_free_port, update_component_status, get_component_status
from .gossip import GossipMesh, GossipPayload, MESH_PROTOCOL_VERSION

# --- ПАРСИНГ АРГУМЕНТОВ ---
parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument("manifest_path")
parser.add_argument("instance_id")
parser.add_argument("--worker", action="store_true")
parser.add_argument("--worker-port", type=int)
parser.add_argument("--gateway-port", type=int)
args, unknown = parser.parse_known_args()

manifest_path = Path(args.manifest_path).resolve()
instance_id = args.instance_id
component_dir = manifest_path.parent
component_root = str(component_dir.parent.resolve())

if component_root not in sys.path: sys.path.insert(0, component_root)
if str(component_dir) not in sys.path: sys.path.insert(0, str(component_dir))

with manifest_path.open("r") as f: manifest = yaml.safe_load(f)

component_title = manifest.get("title", manifest.get("name", "unknown-component"))
component_subtitle = manifest.get("subtitle", manifest.get("description", ""))
start_funcs = manifest.get("start", [])

raw_endpoints = manifest.get("endpoints", {})
endpoints_config = {ep: {"visibility": "public"} for ep in raw_endpoints} if isinstance(raw_endpoints, list) else raw_endpoints
allowed_funcs = set(endpoints_config.keys())
public_funcs = {name for name, cfg in endpoints_config.items() if cfg.get("visibility", "public") == "public"}


if args.worker:
    # =========================================================================
    # РЕЖИМ WORKER: Реализует бизнес-логику и отдает реальные ответы
    # =========================================================================
    @asynccontextmanager
    async def worker_lifespan(app: FastAPI):
        async def _push_schema():
            await asyncio.sleep(0.5) # Даем Шлюзу полсекунды на старт
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(f"http://127.0.0.1:{args.gateway_port}/_sys/schema", json=app.openapi())
            except Exception as e:
                print(f"WARN: Worker failed to push schema to gateway: {e}")
        asyncio.create_task(_push_schema())
        yield

    app = FastAPI(title=component_title + " (Worker)", lifespan=worker_lifespan)
    modules = scan_and_import_endpoints(component_dir, endpoints_config, start_funcs, app, component_root)
    
    if __name__ == "__main__":
        uvicorn.run(app, host="127.0.0.1", port=args.worker_port)

else:
    # =========================================================================
    # РЕЖИМ GATEWAY: Держит публичный порт, проксирует трафик, отдает Mock
    # =========================================================================
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    mesh = GossipMesh(instance_id=instance_id, base_url=base_url)
    
    worker_process = None
    worker_port = None
    worker_active = False
    schema_cache = {}
    
    # Глобальный клиент, чтобы не истощать сокеты при проксировании!
    proxy_client = httpx.AsyncClient()
    
    def spawn_worker():
        global worker_process, worker_port, worker_active
        # Ищем порт СТРОГО начиная со следующего, чтобы не перехватить порт Шлюза
        worker_port = find_free_port(start=port + 1)
        cmd = [sys.executable, "-u", "-m", "mvp.autorouter", str(manifest_path), instance_id, 
               "--worker", "--worker-port", str(worker_port), "--gateway-port", str(port)]
        worker_process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        worker_active = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        spawn_worker()
        asyncio.create_task(mesh.gossip_loop(get_local_contract_func=build_local_contract))
        asyncio.create_task(mesh.reaper_loop())
        yield
        if worker_process:
            worker_process.terminate()
        await proxy_client.aclose()
        print(f"[{instance_id}] Shutting down Gateway...")

    app = FastAPI(title=component_title, description=component_subtitle, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    update_component_status(component_title, component_subtitle, list(allowed_funcs), port, [], instance_id)

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
        raw_timeout = mock_cfg.get("timeout", 15.0) if mock_cfg else 15.0
        httpx_timeout = None if raw_timeout == 0 else float(raw_timeout)
        
        async def route_proxy(request: Request, response: Response):
            async def fallback(err_msg):
                print(f"⚠️  [MOCK TIER] /{ep_name} fallback triggered: {err_msg}")
                response.headers["X-MVP-Mock-Fallback"] = "true"
                response.headers["X-MVP-Original-Error"] = str(err_msg)
                return resolve_mock(ep_name, mock_cfg) if mock_cfg else {"error": err_msg}

            if not worker_active or not worker_port:
                return await fallback("Worker is manually stopped or updating")

            body = await request.body()
            headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
            
            try:
                resp = await proxy_client.request(
                    method=request.method,
                    url=f"http://127.0.0.1:{worker_port}/{ep_name}",
                    content=body, headers=headers, timeout=httpx_timeout
                )
                if resp.status_code >= 500:
                    return await fallback(f"Worker returned HTTP {resp.status_code}")
                return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
            except Exception as e:
                return await fallback(str(e))
        return route_proxy

    for ep_name, ep_cfg in endpoints_config.items():
        if ep_cfg.get("visibility", "public") == "public":
            app.post(f"/{ep_name}", name=ep_name)(make_proxy(ep_name, ep_cfg))

    @app.post("/_sys/rm", include_in_schema=False)
    def sys_rm():
        global worker_active, worker_process
        worker_active = False
        if worker_process: worker_process.terminate()
        return {"status": "worker stopped, mock tier activated"}

    @app.post("/_sys/update", include_in_schema=False)
    def sys_update():
        global worker_process, worker_active
        worker_active = False
        if (component_dir / ".git").exists():
            print(f"[{instance_id}] Updating sources via git pull...")
            subprocess.run(["git", "pull"], cwd=str(component_dir))
        else:
            print(f"[{instance_id}] Local directory detected, just reloading worker...")
            
        if worker_process:
            worker_process.terminate()
            worker_process.wait()
        spawn_worker()
        return {"status": "worker updated and restarted"}

    @app.post("/_sys/schema", include_in_schema=False)
    async def sys_schema(request: Request):
        global schema_cache
        schema_cache = await request.json()
        return {"status": "schema received"}

    def build_local_contract():
        schema = copy.deepcopy(schema_cache) if schema_cache else copy.deepcopy(app.openapi())
        if "info" not in schema: schema["info"] = {}
        schema["info"]["title"] = component_title
        schema["info"]["description"] = component_subtitle
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
        for instance_id, state in mesh.registry.items():
            info = state.contract.get("info", {})
            module_entry = {
                "instance_id": instance_id, "title": info.get("title", "Unknown Module"),
                "subtitle": info.get("description", ""), "base_url": state.base_url,
                "endpoints": state.contract.get("paths", {})
            }
            global_schema["modules"].append(module_entry)
            for schema_name, schema_def in state.contract.get("components", {}).get("schemas", {}).items():
                global_schema["components"]["schemas"][schema_name] = schema_def
        return global_schema

    if __name__ == "__main__":
        config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

