import sys
import copy
import subprocess
from pathlib import Path

# Make sys.path to see root directory of the component as a parent package
manifest_path = sys.argv[1]
component_root = str(Path(manifest_path).parent.resolve())
if component_root not in sys.path:
    sys.path.insert(0, component_root)

import os
import inspect
import importlib.util
from fastapi import FastAPI
from fastapi.responses import (PlainTextResponse, StreamingResponse)
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
import uvicorn
import asyncio
import ast
import importlib.util
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

# Каталог компонента = каталог, где лежит манифест
component_dir = manifest_path.parent

# Добавляем component_dir в sys.path — чтобы импорты работали
if str(component_dir) not in sys.path:
    sys.path.insert(0, str(component_dir))



### Loading and processing contract file ###
with manifest_path.open("r") as f:
    manifest = yaml.safe_load(f)

# Читаем новые поля (с fallback на старые, чтобы не сломать текущие компоненты)
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

# Create FastAPI server ===
# FastAPI автоматически положит их в секцию "info", но мы еще добавим их в корень
app = FastAPI(title=component_title, description=component_subtitle)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
modules = scan_and_import_endpoints(component_dir, endpoints_config, start_funcs, app, component_root)



@app.post("/contract")
def intro_manifest():
    """
    Возвращает OpenAPI JSON, отфильтрованный для public эндпоинтов,
    плюс глобальные title и subtitle на верхнем уровне.
    """
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

port = find_free_port()
update_component_status(component_title, component_subtitle, list(allowed_funcs), port, modules, instance_id)

async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())

