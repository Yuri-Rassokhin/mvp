import sys
import copy
import asyncio
import subprocess
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Response, Request, HTTPException

from .gossip import GossipMesh, GossipPayload, MESH_PROTOCOL_VERSION

# Make sys.path to see root directory of the component as a parent package
manifest_path = sys.argv[1]
component_root = str(Path(manifest_path).parent.resolve())
if component_root not in sys.path:
    sys.path.insert(0, component_root)

import os
import inspect
import importlib.util
from fastapi import FastAPI, Request
from fastapi.responses import (PlainTextResponse, StreamingResponse)
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
import uvicorn
import ast
from types import ModuleType
from typing import Set, List

from .endpoints import scan_and_import_endpoints
from .mesh import (find_free_port, update_component_status, get_component_status)

# check CLI options
if len(sys.argv) < 3:
    print("usage: python autorouter.py /path/to/contract_file.yaml <instance_id>")
    sys.exit(1)

manifest_path = Path(sys.argv[1]).resolve()
instance_id = sys.argv[2]

if not manifest_path.is_file():
    print(f"error: File {manifest_path} not found")
    sys.exit(1)

component_dir = manifest_path.parent

if str(component_dir) not in sys.path:
    sys.path.insert(0, str(component_dir))

### Loading and processing contract file ###
with manifest_path.open("r") as f:
    manifest = yaml.safe_load(f)

component_title = manifest.get("title", manifest.get("name", "unknown-component"))
component_subtitle = manifest.get("subtitle", manifest.get("description", ""))
start_funcs = manifest.get("start", [])

raw_endpoints = manifest.get("endpoints", {})
if isinstance(raw_endpoints, list):
    endpoints_config = {ep: {"visibility": "public"} for ep in raw_endpoints}
else:
    endpoints_config = raw_endpoints

allowed_funcs = set(endpoints_config.keys())
public_funcs = {
    name for name, cfg in endpoints_config.items() 
    if cfg.get("visibility", "public") == "public"
}

# --- ШАГ 1: Инициализация переменных и Mesh ---
port = find_free_port()
base_url = f"http://127.0.0.1:{port}"
mesh = GossipMesh(instance_id=instance_id, base_url=base_url)



# --- ШАГ 2: Определение Lifespan хука ---



# Launch Gossip processes at startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(mesh.gossip_loop(get_local_contract_func=build_local_contract))
    asyncio.create_task(mesh.reaper_loop())
    
    yield # Сервер сразу открывает порт
    
    print(f"[{instance_id}] Shutting down...")
    


# --- ШАГ 3: Создание FastAPI ---
app = FastAPI(title=component_title, description=component_subtitle, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

modules = scan_and_import_endpoints(component_dir, endpoints_config, start_funcs, app, component_root)
update_component_status(component_title, component_subtitle, list(allowed_funcs), port, modules, instance_id)

# --- ШАГ 4: Регистрация роутов ---

def build_local_contract():
    """Чистая функция генерации контракта (без HTTP-контекста)"""
    schema = copy.deepcopy(app.openapi())

    if "info" not in schema:
        schema["info"] = {}

    schema["info"]["title"] = component_title
    schema["info"]["description"] = component_subtitle
    schema["info"].pop("version", None)

    filtered_paths = {}
    for path, path_item in schema.get("paths", {}).items():
        ep_name = path.lstrip("/")
        if ep_name in public_funcs:
            filtered_paths[path] = path_item

    schema["paths"] = filtered_paths
    return schema



@app.post("/contract")
async def intro_manifest(response: Response, request: Request):
    # Принудительно читаем тело, чтобы FastAPI не пытался его парсить как JSON и не падал
    await request.body() 
    response.headers["X-Mesh-Version"] = MESH_PROTOCOL_VERSION
    return build_local_contract()



@app.post("/_gossip", include_in_schema=False)
async def receive_gossip(payload: GossipPayload, request: Request):
    """Скрытый эндпоинт, который принимают стейты от других модулей"""

    # Жестко отклоняем слухи от модулей с другой версией протокола (или без нее)
    client_version = request.headers.get("x-mesh-version")
    if client_version != MESH_PROTOCOL_VERSION:
         raise HTTPException(
             status_code=426, # Upgrade Required
             detail=f"Incompatible mesh protocol version. Expected {MESH_PROTOCOL_VERSION}"
         )

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
        for line in iter(process.stdout.readline, b''):
            yield line.decode("utf-8")

    return StreamingResponse(event_stream(), media_type="text/plain")

@app.api_route("/network", methods=["GET", "POST"], include_in_schema=False)
def get_global_network():
    """
    Отдает сырой словарь всех модулей в сети и их эндпоинтов.
    Может использоваться другими модулями для Client-Side роутинга.
    """
    return {i_id: state.model_dump() for i_id, state in mesh.registry.items()}



@app.api_route("/openapi", methods=["GET", "POST"])
def get_global_openapi():
    """Склеивает контракты всех живых модулей в формат legacy MVP Manager"""
    
    # 1. Формируем костяк старого формата
    global_schema = {
        "title": "MVP Framework Manager",
        "modules": [],
        "components": {
            "schemas": {}
        }
    }

    for instance_id, state in mesh.registry.items():
        # Извлекаем метаданные из локального контракта (OpenAPI формата)
        info = state.contract.get("info", {})
        title = info.get("title", "Unknown Module")
        subtitle = info.get("description", "")
        
        # 2. Формируем объект модуля в старом формате
        module_entry = {
            "instance_id": instance_id,
            "title": title,
            "subtitle": subtitle,
            "base_url": state.base_url,
            "endpoints": {}
        }
        
        # Переносим эндпоинты (paths -> endpoints)
        module_paths = state.contract.get("paths", {})
        for path, path_info in module_paths.items():
            module_entry["endpoints"][path] = path_info
            
        global_schema["modules"].append(module_entry)
        
        # 3. Сливаем схемы (schemas) в единый глобальный пул
        # Это критично для фронтенда и кодогенерации
        module_components = state.contract.get("components", {})
        module_schemas = module_components.get("schemas", {})
        
        for schema_name, schema_def in module_schemas.items():
            # Если схема уже есть, просто перезаписываем (предполагаем, 
            # что стандартные схемы вроде HTTPValidationError идентичны)
            global_schema["components"]["schemas"][schema_name] = schema_def

    return global_schema

# --- ШАГ 5: Запуск сервера ---

async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
