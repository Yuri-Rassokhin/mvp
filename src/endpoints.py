import os
import sys
import inspect
import importlib.util
import ast
from fastapi import FastAPI
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
import socket
import uvicorn



# Returns True if file has unsafe code in global scope, False otherwise
def has_top_level_code(filepath):
    with open(filepath, "r") as f:
        node = ast.parse(f.read(), filename=filepath)
        for stmt in node.body:
            if not isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr)):
                return True
    return False



# Return list of files with endpoint functions
def find_candidate_files(component_dir, allowed_funcs):
    candidates = []
    for root, _, files in os.walk(component_dir):
        for filename in files:
            if filename.endswith(".py"):
                path = os.path.join(root, filename)
                try:
                    with open(path, "r") as f:
                        tree = ast.parse(f.read(), filename=path)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.FunctionDef) and node.name in allowed_funcs:
                                candidates.append(path)
                                break
                except Exception as e:
                    print(f"❌ Failed to parse {path}: {e}")
    return candidates



def safe_import_module(filepath, component_root):
    filepath = Path(filepath).resolve()
    component_root = Path(component_root).resolve()

    # Превращаем путь в dotted import, например:
    # /home/opc/medical-symptome-preprocessor/src/endpoint/endpoint.py
    # → endpoint.endpoint
    relative = filepath.relative_to(component_root)
    module_name = ".".join(relative.with_suffix("").parts)

    # Добавляем component_root в sys.path
    if str(component_root) not in sys.path:
        sys.path.insert(0, str(component_root))

    module = importlib.import_module(module_name)
    return module



# === Сканируем нужные модули и создаём endpoint'ы ===
def scan_and_import_endpoints(component_dir: str, allowed_funcs: set, start_funcs: list, app: FastAPI, component_root: str):
    modules = []
    candidate_files = find_candidate_files(component_dir, allowed_funcs)

    for filepath in candidate_files:
        print(f"Converting {filepath}")

        if has_top_level_code(filepath):
            raise RuntimeError(f"❌ Unsafe global code found in: {filepath}. Aborting.")

        try:
            module = safe_import_module(filepath, component_dir)
        except Exception as e:
            print(f"❌ Failed to import {filepath}: {e}")
            continue

        modules.append(module)

        for fname in start_funcs:
            if hasattr(module, fname):
                try:
                    getattr(module, fname)()
                    print(f"✅ Start function '{fname}' executed from {filepath}")
                except Exception as e:
                    print(f"❌ Start function '{fname}' failed in {filepath}: {e}")

        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name in allowed_funcs:
                sig = inspect.signature(func)
                fields = {}
                for param_name, param in sig.parameters.items():
                    ann = param.annotation if param.annotation != inspect._empty else str
                    fields[param_name] = (ann, ...)
                Model = create_model(f"{name.title()}Input", **fields)

                async def endpoint(data: Model, _func=func):
                    return _func(**data.dict())

                app.post(f"/{name}", name=name)(endpoint)
                print(f"✅ Endpoint /{name} activated from {filepath}")

    return modules


