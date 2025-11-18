import socket
from pathlib import Path
import json
import inspect
from typing import Optional, List, Dict



def find_free_port(start=8500, end=8999):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found in the range")



def get_component_status(component: Optional[str] = None) -> List[Dict]:
    """
    Возвращает список компонент из ~/.mvp/status.
    Если передан component (по name), фильтрует по нему.
    """
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        return []

    try:
        with open(status_path, "r") as f:
            raw = f.read().strip()
            if not raw:
                return []

            components = json.loads(raw)
            if not isinstance(components, list):
                raise ValueError("status file must contain a list")
    except Exception as e:
        raise RuntimeError(f"Failed to load status file: {e}")

    if component:
        return [entry for entry in components if entry.get("id") == component]

    return components



def type_name(annotation):
    try:
        return annotation.__name__
    except AttributeError:
        return str(annotation).replace("typing.", "")



import inspect
from typing import Any

def type_name(annotation: Any) -> str:
    try:
        return annotation.__name__
    except AttributeError:
        return str(annotation).replace("typing.", "")



def get_signatures(modules, endpoints):
    signatures = {}

    for fname in endpoints:
        func = None
        for module in modules:
            func = getattr(module, fname, None)
            if func:
                break  # нашли, дальше не ищем

        if not func:
            continue  # не нашли ни в одном модуле

        sig = inspect.signature(func)

        args = {
            k: type_name(v.annotation) if v.annotation != inspect._empty else "str"
            + (f" = {v.default!r}" if v.default != inspect._empty else "")
            for k, v in sig.parameters.items()
        }

        signatures[fname] = {
            "inputs": args,
            "returns": "JSON"
        }

    return signatures



def update_component_status(name, description, endpoints, port, modules, instance_id):
    endpoints.extend([ "system-manifest", "system-log", "system-stream"])
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

    st = {
        "name": name,
        "id": instance_id,
        "description": description,
        "endpoints": endpoints,
        "port": port,
        "ip": socket.gethostbyname(socket.gethostname()),
        "io": get_signatures(modules, endpoints)
    }

    updated = False

    for entry in status:
        if entry.get("id") == instance_id:
            entry.update(st)
            updated = True
            break
    
    if not updated:
        status.append(st)

    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)

