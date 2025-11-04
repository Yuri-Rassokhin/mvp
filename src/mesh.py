import socket
from pathlib import Path
import json

def find_free_port(start=8500, end=8999):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found in the range")



def get_signatures(module, endpoints):
    signatures = {}
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

        signatures[fname] = {
            "inputs": args,
            "returns": "JSON"
        }

    return signatures



def update_component_status(name, description, endpoints, port, module, instance_id):
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
        "io": get_signatures(module, endpoints)
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

