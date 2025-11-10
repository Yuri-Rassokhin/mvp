import sys
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
import ast
import importlib.util
from types import ModuleType
from typing import Set, List
from endpoints import (find_candidate_files, has_top_level_code, scan_and_import_endpoints)
from mesh import (find_free_port, update_component_status, get_component_status)
from modules import (load_and_register_module, is_safe_module, scan_and_register)


# check CLI options
if len(sys.argv) < 3:
    print("usage: python autorouter.py /path/to/manifest.yaml <instance_id>")
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

# === Загрузка манифеста ===
with manifest_path.open("r") as f:
    manifest = yaml.safe_load(f)

component_name = manifest.get("name", "unknown-component")
description = manifest.get("description", "")
allowed_funcs = set(manifest.get("endpoints", []))
start_funcs = manifest.get("start", [])

# Create FastAPI server ===
app = FastAPI(title=component_name, description=description)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
modules = scan_and_import_endpoints(component_dir, allowed_funcs, start_funcs, app, component_root)



@app.get("/intro/manifest")
def intro_manifest():
    """
    Возвращает runtime-манифест для текущего instance из ~/.mvp/status
    """
    global instance_id
    print(f"Instance: {instance_id}")

    try:
        return get_component_status(instance_id)
    except Exception as e:
        return {"error": str(e)}



@app.get("/output/log", response_class=PlainTextResponse)
def get_log():
    result = subprocess.run(["mvp", "log", instance_id], capture_output=True, text=True)
    return result.stdout

@app.get("/output/stream")
def stream_log():
    process = subprocess.Popen(["mvp", "log", instance_id, "-f"], stdout=subprocess.PIPE)

    def event_stream():
        for line in iter(process.stdout.readline, b''):
            yield line.decode("utf-8")

    return StreamingResponse(event_stream(), media_type="text/plain")

port = find_free_port()
update_component_status(component_name, description, list(allowed_funcs), port, modules, instance_id)
uvicorn.run(app, host="0.0.0.0", port=port)

