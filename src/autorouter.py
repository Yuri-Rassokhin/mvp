# cmesh_server/autorouter.py

import os
import sys
import inspect
import importlib.util
from fastapi import FastAPI
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware

# === 0. Аргумент: путь к манифесту ===
if len(sys.argv) < 2:
    print("Usage: python autorouter.py /path/to/cmesh.yaml")
    sys.exit(1)

manifest_path = sys.argv[1]
if not os.path.isfile(manifest_path):
    print(f"[cmesh] Error: File {manifest_path} not found")
    sys.exit(1)

# Каталог компонента = каталог, где лежит манифест
component_dir = os.path.dirname(os.path.abspath(manifest_path))
sys.path.insert(0, component_dir)

# === 1. Загрузка манифеста ===
with open(manifest_path, "r") as f:
    manifest = yaml.safe_load(f)

allowed_funcs = set(manifest.get("endpoints", []))

# === 2. FastAPI-приложение ===
app = FastAPI(
    title=manifest.get("name", "cmesh-component"),
    description=manifest.get("description", "")
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# === 3. Сканируем все .py-файлы в каталоге ===
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
        print(f"[MVP] Error loading {filename}: {e}")
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
            kwargs = data.dict()
            return _func(**kwargs)

        app.post(f"/{name}", name=name)(endpoint)
        print(f"[MVP] Endpoint /{name} activated from {filename}")

