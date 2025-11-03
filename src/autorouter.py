import sys
from pathlib import Path

manifest_path = sys.argv[1]
component_root = str(Path(manifest_path).parent.resolve())

# Добавим в sys.path корень компонента, как корневой пакет
if component_root not in sys.path:
    sys.path.insert(0, component_root)

import os
import inspect
import importlib.util
from fastapi import FastAPI
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
import socket
import uvicorn
import ast
import importlib.util
from types import ModuleType
from typing import Set, List
from util import (
    find_candidate_files,
    has_top_level_code,
    scan_and_import_endpoints
)



def load_and_register_module(filepath: str, allowed_funcs: set, start_funcs: list, app: FastAPI):
    # filepath: /home/opc/medical-symptome-preprocessor/src/endpoint/endpoint.py
    # relative_path: src/endpoint/endpoint.py
    relative_path = os.path.relpath(filepath, component_root).replace("/", ".").replace("\\", ".")
    modulename = relative_path[:-3]  # remove .py

    module = importlib.import_module(modulename)

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"❌ Error loading {filepath}: {e}")
        return None

    # Выполняем start-функции
    for func_name in start_funcs:
        if hasattr(module, func_name):
            try:
                getattr(module, func_name)()
                print(f"🔁 start function '{func_name}' executed from {filepath}")
            except Exception as e:
                print(f"⚠️ error running start function '{func_name}': {e}")
        else:
            print(f"⚠️ start function '{func_name}' not found in {filepath}")

    # Регистрируем endpoint'ы
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
        print(f"✅ endpoint /{name} activated from {filepath}")

    return module



def is_safe_module(filepath: str, allowed_funcs: set) -> bool:
    import ast
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception as e:
        print(f"⚠️ cannot parse {filepath}: {e}")
        return False

    # Ищем нужные функции
    func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if not any(f in func_names for f in allowed_funcs):
        return False

    # Проверяем, нет ли опасных действий на верхнем уровне
    for node in tree.body:
        # разрешаем только безопасные конструкции
        if isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.ClassDef, ast.Assign, ast.AnnAssign)):
            continue
        # разрешаем docstring
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Str):
            continue
        # всё остальное считаем подозрительным
        print(f"🚫 Skipping {filepath} — unsafe top-level code: {type(node).__name__}")
        return False

    return True



def scan_and_register(component_dir: str, allowed_funcs: set, start_funcs: list, app):
    """
    Рекурсивно обходит все .py-файлы внутри component_dir,
    проверяет, безопасны ли они, и регистрирует функции как endpoint'ы.
    Возвращает список успешно загруженных модулей.
    """
    loaded_modules = []

    for root, _, files in os.walk(component_dir):
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("__") or filename == os.path.basename(__file__):
                continue

            filepath = os.path.join(root, filename)

            # Пропускаем очевидные служебные папки
            if "venv" in filepath or "tests" in filepath or "site-packages" in filepath:
                continue

            # Безопасность через AST-анализ
            if not is_safe_module(filepath, allowed_funcs):
                print(f"⛔ Skipping unsafe module: {filepath}")
                continue

            print(f"✅ Registering safe module: {filepath}")
            module = load_and_register_module(filepath, allowed_funcs, start_funcs, app)

            if module is not None:
                loaded_modules.append(module)

    return loaded_modules



def find_free_port(start=8500, end=8999):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found in the range")



def update_component_status(name, description, endpoints, port, module):
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

    # Получаем сигнатуры входов/выходов для функций из этого компонента
    io_signatures = {}
    for fname in endpoints:
        func = getattr(module, fname, None)
        if not func:
            continue

        sig = inspect.signature(func)

        args = {
            k: getattr(v.annotation, "__name__", str(v.annotation)) if v.annotation != inspect._empty else "str"
# this option disallows List and such
#            k: str(v.annotation.__name__) if v.annotation != inspect._empty else "str"
            for k, v in sig.parameters.items()
        }

        io_signatures[fname] = {
            "inputs": args,
            "returns": "JSON"
        }

    for entry in status:
        if entry.get("id") == instance_id:
            entry.update({
                "name": name,
                "description": description,
                "endpoints": endpoints,
                "port": port,
                "ip": socket.gethostbyname(socket.gethostname()),
                "io": io_signatures
            })
            break
    else:
        status.append({
            "id": instance_id,
            "name": name,
            "description": description,
            "endpoints": endpoints,
            "port": port,
            "ip": socket.gethostbyname(socket.gethostname()),
            "io": io_signatures
        })

    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)



# === Аргумент: путь к манифесту ===
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


# === Создание FastAPI-приложения ===
app = FastAPI(title=component_name, description=description)


app.add_middleware(
CORSMiddleware,
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"]
)


# === Сканируем .py-файлы на предмет функций ===
modules = scan_and_import_endpoints(component_dir, allowed_funcs, start_funcs, app, component_root)
port = find_free_port()
update_component_status(component_name, description, list(allowed_funcs), port, modules)
uvicorn.run(app, host="0.0.0.0", port=port)

