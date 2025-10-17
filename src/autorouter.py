import os
import sys
import inspect
import importlib.util
from fastapi import FastAPI
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
import socket
import uvicorn

# === Аргумент: путь к манифесту ===
if len(sys.argv) < 3:
    print("usage: python autorouter.py /path/to/manifest.yaml <instance_id>")
    sys.exit(1)

manifest_path = sys.argv[1]
instance_id = sys.argv[2]

if not os.path.isfile(manifest_path):
    print(f"error: File {manifest_path} not found")
    sys.exit(1)

# Каталог компонента = каталог, где лежит манифест
component_dir = os.path.dirname(os.path.abspath(manifest_path))
sys.path.insert(0, component_dir)

# === Загрузка манифеста ===
with open(manifest_path, "r") as f:
    manifest = yaml.safe_load(f)

component_name = manifest.get("name", "unknown-component")
description = manifest.get("description", "")
allowed_funcs = set(manifest.get("endpoints", []))

# === Создание FastAPI-приложения ===
app = FastAPI(title=component_name, description=description)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# === Сканируем .py-файлы на предмет функций ===
for filename in os.listdir(component_dir):
    if not filename.endswith(".py") or filename == os.path.basename(__file__):
        continue

    filepath = os.path.join(component_dir, filename)
    modulename = filename[:-3]

    spec = importlib.util.spec_from_file_location(modulename, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modulename] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"error loading {filename}: {e}")
        continue

    for name, func in inspect.getmembers(module, inspect.isfunction):
        if name not in allowed_funcs:
            continue

        sig = inspect.signature(func)
        fields = {}
        for param_name, param in sig.parameters.items():
            ann = param.annotation if param.annotation != inspect._empty else str
            fields[param_name] = (ann, ...)

        Model = create_model(f"{name.title()}Input", **fields)

        async def endpoint(data: Model, _func=func):
            return _func(**data.dict())

        app.post(f"/{name}", name=name)(endpoint)
        print(f"endpoint /{name} activated from {filename}")

# === Поиск свободного порта ===
def find_free_port(start=8500, end=8999):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found in the range")

port = find_free_port()

# === Сохраняем статус ===
def update_component_status(name, description, endpoints, port):
    status_path = Path.home() / ".mvp" / "status"
    status_path.parent.mkdir(parents=True, exist_ok=True)

    if status_path.exists():
        try:
            with open(status_path, "r") as f:
                status = json.load(f)
            if not isinstance(status, list):
                status = []
        except:
            status = []
    else:
        status = []

    for entry in status:
        if entry.get("id") == instance_id:
            entry.update({
                "name": name,
                "description": description,
                "endpoints": endpoints,
                "port": port,
                "ip": "0.0.0.0"
            })
            break
    else:
        status.append({
            "id": instance_id,
            "name": name,
            "description": description,
            "endpoints": endpoints,
            "port": port,
            "ip": "0.0.0.0"
        })

    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)

# === Применяем и запускаем ===
update_component_status(component_name, description, list(allowed_funcs), port)
uvicorn.run(app, host="0.0.0.0", port=port)

