from pathlib import Path
from typing import Annotated
from typing import List
import typer
import os
import yaml
import signal
import subprocess
import sys
import uuid
import json
from rich.table import Table
from rich.console import Console
from typing import Optional
from server.oracle import add_instance, remove_instance
import time

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")

import time
import json
from pathlib import Path

import time

def tail_log_until_uvicorn_ready(log_path: Path, timeout: int = 180, poll_interval: float = 0.5) -> bool:
    """
    Читает лог в реальном времени, пока не появится строка от Uvicorn.
    Возвращает True, если удалось дождаться запуска, False — если по таймауту.
    """
    deadline = time.time() + timeout
    seen_uvicorn = False
    position = 0

    print("⏳ Waiting for autorouter to start...")

    while time.time() < deadline:
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(position)
                lines = f.readlines()
                position = f.tell()

                for line in lines:
                    line = line.strip()
                    print(line)
                    if "Uvicorn running on" in line:
                        seen_uvicorn = True
                        break

        if seen_uvicorn:
            print("✅ autorouter started (Uvicorn is running)")
            return True

        time.sleep(poll_interval)

    print("⚠ autorouter startup timeout — continuing anyway")
    return False



def wait_for_instance_in_status(instance_id: str, timeout=5.0):
    """
    Ждет, пока запись об instance появится в файле ~/.mvp/status.
    """
    status_path = Path.home() / ".mvp" / "status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not status_path.exists():
            time.sleep(0.1)
            continue
        try:
            with open(status_path, "r") as f:
                status = json.load(f)
            for entry in status:
                if entry.get("id") == instance_id:
                    return entry
        except Exception:
            pass
        time.sleep(180)
    raise RuntimeError(f"⏳ Timeout: instance {instance_id} not found in status after {timeout}s")

@app.command()
def add(component: str):
    """
    Deploy new instance of a component to its tier environment.
    """
    base_dir = Path(__file__).parent.parent.resolve()
    manifest_path = Path(component).expanduser().resolve()

    import subprocess
    import uuid

    if not manifest_path.exists():
        typer.echo(f"❌ Manifest not found: {manifest_path}")
        raise typer.Exit(1)

    try:
        with open(manifest_path, "r") as f:
            manifest = yaml.safe_load(f)
    except Exception as e:
        typer.echo(f"❌ Failed to parse manifest as YAML: {e}")
        raise typer.Exit(1)

    # ✅ Валидация содержимого YAML
    if not isinstance(manifest, dict):
        typer.echo("❌ Manifest must be a YAML dictionary")
        raise typer.Exit(1)

    required_keys = ["name", "endpoints"]
    for key in required_keys:
        if key not in manifest:
            typer.echo(f"❌ Manifest missing required key: {key}")
            raise typer.Exit(1)

    if not isinstance(manifest["endpoints"], list) or not all(isinstance(x, str) for x in manifest["endpoints"]):
        typer.echo("❌ 'endpoints' must be a list of strings")
        raise typer.Exit(1)

    if "start" in manifest:
        if not isinstance(manifest["start"], list) or not all(isinstance(x, str) for x in manifest["start"]):
            typer.echo("❌ 'start' must be a list of strings if specified")
            raise typer.Exit(1)

    if "description" not in manifest:
        typer.echo("⚠️  Warning: Manifest has no 'description'")

    # Install dependencies if requirements.txt is present
    req_file = base_dir / "requirements.txt"
    if req_file.exists():
        typer.echo("installing dependencies")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)

    # generate unique log for the current instance of the component
    component_name = Path(component).stem
    unique_id = uuid.uuid4().hex  # 32 символа, уникальный
    log_dir = Path.home() / ".mvp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Запускаем процесс — пока без PID
    log_file_base = log_dir / f"mvp-{unique_id}"
    tmp_log_path = log_file_base.with_suffix(".tmp")  # временный лог, до получения PID

    with open(tmp_log_path, "ab") as out:
        process = subprocess.Popen(
            [sys.executable, "-u", str(base_dir / "src" / "autorouter.py"), str(manifest_path), unique_id],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True
        )

    tail_log_until_uvicorn_ready(tmp_log_path)
    pid = process.pid
    final_log_path = log_dir / f"mvp-{unique_id}-pid{pid}.log"
    tmp_log_path.rename(final_log_path)

    # 🔽 Ждем появления записи в статусе
    try:
        instance_record = wait_for_instance_in_status(unique_id)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    endpoint_strings = []
    for ep in instance_record["endpoints"]:
        sig = instance_record.get("io", {}).get(ep, {}).get("inputs", {})
        formatted_sig = ", ".join(f"{k}: {v}" for k, v in sig.items())
        endpoint_strings.append(f"{ep} {{{formatted_sig}}}")

    add_instance(
        instance_record['name'],
        unique_id,
        instance_record['description'],
        f"http://{instance_record['ip']}:{instance_record['port']}",
        endpoint_strings
    )

    typer.echo(f"instance {unique_id} of the component {component_name} launched")

@app.command()
def rm(instance: str):
    """
    Stop a component and delete it from the registry
    """
    from pathlib import Path
    import json

    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        typer.echo("status file not found")
        raise typer.Exit(1)

    try:
        with open(status_path, "r") as f:
            status = json.load(f)
    except Exception as e:
        typer.echo(f"❌ Failed to load status file: {e}")
        raise typer.Exit(1)

    # Поиск PID по instance_id через ps
    try:
        ps_out = subprocess.check_output(["ps", "aux"], text=True)
        for line in ps_out.splitlines():
            if instance in line and "autorouter.py" in line:
                parts = line.split()
                pid = int(parts[1])
                os.kill(pid, signal.SIGTERM)
                break
        else:
            typer.echo(f"instance {instance} is not running")
    except Exception as e:
        typer.echo(f"failed to search or kill instance process: {e}")

    original_len = len(status)
    status = [entry for entry in status if entry.get("id") != instance]

    if len(status) == original_len:
        typer.echo(f"instance {instance} not found")
    else:
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)

    try:
        remove_instance(instance)
        typer.echo(f"instance {instance} stopped and removed from MVP registry")
    except Exception as e:
        typer.echo(f"instance {instance} stopped, but failed to remove from database: {e}")

@app.command()
def ls(component: Optional[str] = None):
    """
    Show status of a specific component or all deployed instances.
    """
    import json
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        typer.echo("MVP registry is empty")
        raise typer.Exit()

    try:
        with open(status_path, "r") as f:
            raw = f.read().strip()
            if not raw:
                components = []
            else:
                components = json.loads(raw)
            if not isinstance(components, list):
                raise ValueError("status file must contain a list")
    except Exception as e:
        typer.echo(f"❌ Failed to load status file: {e}")
        raise typer.Exit(1)

    filtered = [
        entry for entry in components
        if component is None or entry.get("name") == component
    ]

    if not filtered:
        if component:
            typer.echo(f"no instances found for component: {component}")
        else:
            typer.echo("MVP registry is empty")
        raise typer.Exit()

    for entry in filtered:
        typer.echo("────────────────────────────────────────")
        typer.echo(f"Component    {entry.get('name', '[unknown]')}")
        typer.echo(f"Instance     {entry.get('id', '[unknown]')}")
        typer.echo(f"Description  {entry.get('description', '')}")

        ip = entry.get("ip", "0.0.0.0")
        port = entry.get("port", "")
        endpoints = entry.get("endpoints", [])
        io = entry.get("io", {})

        if endpoints:
            for ep in endpoints:
                sig = io.get(ep, {})
                inputs = sig.get("inputs", {})
                arg_str = ", ".join(f"\"{k}\": {v}" for k, v in inputs.items()) if inputs else ""
                typer.echo(f"http://{ip}:{port}/{ep}, {arg_str}")
        else:
            typer.echo("URL not found")

from typing import List

@app.command()
def call(
    target: str,
    endpoint: str,
    json_parts: Optional[List[str]] = typer.Argument(None, help="Optional JSON payload split into parts")
):
    """
    Call an endpoint on a deployed instance (by ID or component name), optionally passing JSON input.
    """
    import requests
    import json

    json_data = None
    if json_parts:
        json_str = " ".join(json_parts)
        try:
            json_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ Invalid JSON: {e}")
            raise typer.Exit(1)

    # читаем статус
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        typer.echo("❌ Status file not found.")
        raise typer.Exit(1)

    with open(status_path, "r") as f:
        status = json.load(f)

    # ищем по ID или имени
    match = next((s for s in status if s.get("id") == target or s.get("name") == target), None)
    if not match:
        typer.echo(f"❌ No instance found with ID or name '{target}'")
        raise typer.Exit(1)

    port = match.get("port")
    if not port:
        typer.echo(f"❌ No port found for instance '{target}'")
        raise typer.Exit(1)

    url = f"http://localhost:{port}/{endpoint}"

    try:
        resp = requests.post(url, json=json_data)
        typer.echo(f"{resp.text}")
    except Exception as e:
        typer.echo(f"❌ Request failed: {e}")
        raise typer.Exit(1)

@app.command()
def log(id: str):
    """
    Show log output of a specific component instance by ID.
    """
    from pathlib import Path

    log_dir = Path.home() / ".mvp" / "logs"
    if not log_dir.exists():
        typer.echo("❌ Log directory not found")
        raise typer.Exit(1)

    # Находим соответствующий лог-файл
    matching_logs = list(log_dir.glob(f"mvp-{id}-pid*.log"))
    if not matching_logs:
        typer.echo(f"❌ No log file found for ID {id}")
        raise typer.Exit(1)

    log_file = matching_logs[0]
    typer.echo(f"📄 Showing log: {log_file}\n{'─'*40}")
    try:
        with open(log_file, "r") as f:
            typer.echo(f.read())
    except Exception as e:
        typer.echo(f"❌ Failed to read log file: {e}")
        raise typer.Exit(1)

@app.command()
def switch(component: str, tier: str = typer.Argument(..., help="Target tier: mock | prod | preprod")):
    """
    Switch component to given tier.
    """
    if tier not in {"mock", "prod", "preprod"}:
        typer.echo("❌ Error: tier must be one of: mock, prod, preprod")
        raise typer.Exit(1)
    typer.echo(f"🔄 Switching {component} to tier: {tier}")
    # TODO: implement symlink update and/or proxy update

@app.command()
def ask(question: str):
    """
    Ask the AI assistant about available components or their functionality.
    """
    typer.echo(f"🤖 Asking AI: {question}")
    # TODO: integrate OpenAI / Cohere API and RAG over cmesh.yaml registry

if __name__ == "__main__":
    app()

