from fastapi import FastAPI

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




