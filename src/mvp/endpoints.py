import ast
import asyncio
from rich.console import Console
import os
from platform import node
import sys
import inspect
import importlib.util
from fastapi import FastAPI, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import create_model
import yaml
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
import socket
import uvicorn
import types

from . import ast_processor



# Read config with excluded subdirectories such as venv, etc
def get_excluded_dirs() -> set:
    """
    Читает список игнорируемых директорий из ~/.mvp/exclude.
    Возвращает множество (set) названий папок.
    """
    # Базовые исключения, которые спасут от сканирования тяжелых папок по умолчанию
    excludes = {".git", "venv", ".venv", "env", "__pycache__", "node_modules"}

    exclude_file = Path.home() / ".mvp" / "exclude"
    if exclude_file.exists():
        with open(exclude_file, "r") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    excludes.add(cleaned.strip("/"))

    return excludes

# Return list of files with endpoint functions
def find_candidate_files(component_dir, allowed_funcs):
    excludes = get_excluded_dirs()
    candidates = []
    
    for root, dirs, files in os.walk(component_dir):
        # Отсекаем ненужные папки, чтобы os.walk в них даже не заходил
        dirs[:] = [d for d in dirs if d not in excludes]
        
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
                    print(f"ERROR: Failed to parse {path}: {e}")
                    
    return candidates

def load_module_from_ast(filepath, module_name, tree):
    # Компилируем AST в байт-код
    code_obj = compile(tree, filename=filepath, mode='exec')
    
    # Создаем объект модуля
    module = types.ModuleType(module_name)
    module.__file__ = str(filepath)
    module.__name__ = module_name
    
    # Регистрируем в sys.modules, чтобы импорты работали
    sys.modules[module_name] = module
    
    # Выполняем код в пространстве имен модуля
    exec(code_obj, module.__dict__)
    return module

def process_and_load_module(filepath, component_root):
    # 1. Читаем файл один раз
    with open(filepath, "r") as f:
        source = f.read()
    
    # 2. Парсим
    tree = ast.parse(source, filename=filepath)
    
    # 3. Проверяем безопасность (на том же объекте tree)
    check_ast_safe(tree, filepath)

    # 4. Модифицируем AST (process_tree уже готов)
    tree = ast_processor.process_tree(tree, filepath)
    
    # 5. Генерируем уникальное имя (на основе пути, чтобы избежать коллизий)
    rel_path = Path(filepath).relative_to(component_root)
    module_name = "dynamic." + ".".join(rel_path.with_suffix("").parts)
    
    # 6. Загружаем
    return load_module_from_ast(filepath, module_name, tree)

# Вспомогательная проверка AST (без чтения файла)
def check_ast_safe(tree, filepath):
    console = Console(force_terminal=True, width=10000)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Pass)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node.value, (ast.Constant, ast.List, ast.Dict, ast.Tuple)):
                continue
        try:
            offending_code = ast.unparse(node)
            console.print(f"[yellow]WARN: Unsafe global code in {filepath}:[/yellow]")
            console.print(f"\n[yellow][italic]{offending_code}[/italic][/yellow]\n")
        except Exception:
            console.print(f"[yellow]WARN: Unsafe global code in {filepath}: {node}[/yellow]")
        console.print(f"[yellow]WARN: Only imports, function definitions, and simple constant assignments are safe at the global level[/yellow]")
        return False
    return True

# Измененная сигнатура: принимаем endpoints_config (dict) вместо allowed_funcs (set)
def scan_and_import_endpoints(component_dir: str, endpoints_config: dict, start_funcs: list, app: FastAPI, component_root: str):
    allowed_funcs = set(endpoints_config.keys())
    candidate_files = find_candidate_files(component_dir, allowed_funcs)
    modules = []

    console = Console(force_terminal=True, width=10000)
    activated_funcs = set()

    for filepath in candidate_files:
        module = process_and_load_module(filepath, component_root)

        if not module:
            print(f"ERROR: Skipping unsafe file {filepath}")
            continue

        modules.append(module)

        for fname in start_funcs:
            if hasattr(module, fname):
                try:
                    getattr(module, fname)()
                    print(f"INFO: Start function '{fname}' executed from {filepath}")
                except Exception as e:
                    console.print(f"[red]ERROR: Start function '{fname}' failed in {filepath}: {e}[/red]")
                    raise RuntimeError(f"Contract violation: start function '{fname}' failed in {filepath}: {e}")

        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name in allowed_funcs:
                sig = inspect.signature(func)
                fields = {}
                for param_name, param in sig.parameters.items():
                    ann = param.annotation if param.annotation != inspect._empty else str
                    fields[param_name] = (ann, ...)
                Model = create_model(f"{name.title()}Input", **fields)

                return_annotation = sig.return_annotation
                response_model = return_annotation if return_annotation != inspect._empty else None

                ep_config = endpoints_config.get(name, {})

                # Фабрика для создания эндпоинта (изолирует func в замыкании)
                def make_endpoint(target_func, endpoint_config, comp_dir, ret_ann):
                    mock_cfg = endpoint_config.get("mock")

                    # Логика извлечения mock-данных
                    def resolve_mock():
                        if not mock_cfg:
                            return None

                        # Вариант 1: Inline YAML
                        if "value" in mock_cfg:
                            return mock_cfg["value"]

                        # Вариант 2: External File
                        if "path" in mock_cfg:
                            mock_path = os.path.join(comp_dir, mock_cfg["path"])
                            try:
                                with open(mock_path, "r") as f:
                                    if mock_path.endswith(".yaml") or mock_path.endswith(".yml"):
                                        return yaml.safe_load(f)
                                    return json.load(f)
                            except Exception as ex:
                                console.print(f"[red]ERROR: Failed to load mock from {mock_path}: {ex}[/red]")
                                return {"error": f"Missing mock file: {mock_path}"}

                        # Вариант 3: Auto-generation
                        if mock_cfg.get("type") == "auto":
                            try:
                                from polyfactory.factories.pydantic_factory import ModelFactory
                                # Если возвращаемый тип - это Pydantic модель
                                if hasattr(ret_ann, "model_fields") or hasattr(ret_ann, "__fields__"):
                                    class DynamicFactory(ModelFactory):
                                        __model__ = ret_ann
                                    # Возвращаем объект, FastAPI сам сериализует его в JSON
                                    return DynamicFactory.build()
                            except ImportError:
                                console.print("[yellow]WARN: polyfactory is not installed. Fallback auto-mock to {}. Run: pip install polyfactory[/yellow]")
                            except Exception as ex:
                                console.print(f"[yellow]WARN: Auto-mock failed: {ex}. Fallback to {{}}[/yellow]")
                            return {}

                        return None

                    # Кешируем значение при старте сервера, если refresh == False
                    cached_mock = None
                    if mock_cfg and not mock_cfg.get("refresh", False):
                        cached_mock = resolve_mock()

                    # Инжектим Response, чтобы менять HTTP заголовки
                    async def endpoint_handler(data: Model, response: Response):
                        try:
                            # Circuit Breaker: Таймаут 15 секунд на бизнес-логику
                            if inspect.iscoroutinefunction(target_func):
                                return await asyncio.wait_for(target_func(**data.dict()), timeout=15.0)
                            return await asyncio.wait_for(run_in_threadpool(target_func, **data.dict()), timeout=15.0)

                        except Exception as e:
                            # Если есть mock-конфиг — спасаем ситуацию
                            if mock_cfg:
                                console.print(f"[yellow]⚠️  [MOCK TIER] /{name} failed ({type(e).__name__}: {e}). Switching to fallback.[/yellow]")
                                response.headers["X-MVP-Mock-Fallback"] = "true"
                                response.headers["X-MVP-Original-Error"] = str(e)

                                # Обновляем данные, если включен refresh
                                if mock_cfg.get("refresh", False) or cached_mock is None:
                                    return resolve_mock()
                                return cached_mock

                            # Если мока нет — честно падаем
                            raise e

                    return endpoint_handler

                endpoint_wrapper = make_endpoint(func, ep_config, component_dir, return_annotation)

                # Извлекаем визуальные настройки из YAML
                openapi_extra = {}
                if "visual" in ep_config:
                    openapi_extra["x-visual-type"] = ep_config["visual"].get("type")
                    openapi_extra["x-visual-title"] = ep_config["visual"].get("title")
                    openapi_extra["x-visual-comment"] = ep_config["visual"].get("comment")

                # Передаем response_model и openapi_extra в декоратор FastAPI
                app.post(
                    f"/{name}",
                    name=name,
                    response_model=response_model,
                    openapi_extra=openapi_extra if openapi_extra else None
                )(endpoint_wrapper)

                activated_funcs.add(name)
                print(f"INFO: Endpoint /{name} activated from {filepath} (visibility: {ep_config.get('visibility', 'public')})")

    missing_funcs = allowed_funcs - activated_funcs
    for func_name in missing_funcs:
        console.print(f"[red]ERROR: Endpoint '{func_name}' is promised in contract, but missing in codebase[/red]")
        raise RuntimeError(f"Contract violation: endpoint '{func_name}' promised in contract, but missing in codebase")

    return modules

