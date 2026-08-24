import socket
from pathlib import Path
import json
import inspect
from typing import Optional, List, Dict, Any



def get_bound_socket(host="", start=8500, end=8999):
    """Намертво привязывает сокет и возвращает пару (socket, port)"""
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR спасает от зависших портов TIME_WAIT
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            # МЫ НЕ ЗАКРЫВАЕМ СОКЕТ. Возвращаем его открытым!
            return s, port
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"No free ports available in range {start}-{end}")



def find_free_port(start=8500, end=8999):
    """Обертка для совместимости. Не защищает от TOCTOU гонок!"""
    s, port = get_bound_socket(start=start, end=end)
    s.close()
    return port



def type_name(annotation: Any) -> str:
    try:
        return annotation.__name__
    except AttributeError:
        return str(annotation).replace("typing.", "")

def get_signatures(modules, endpoints):
    if not modules: return {}

    signatures = {}

    for fname in endpoints:
        func = None
        for module in modules:
            func = getattr(module, fname, None)
            if func:
                break

        if not func:
            continue

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

def update_component_status(title, subtitle, endpoints, port, modules, instance_id):
    endpoints.extend([ "contract", "syslog", "syslog-stream"])
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
        "title": title,
        "subtitle": subtitle,
        "id": instance_id,
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
