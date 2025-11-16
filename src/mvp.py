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
from file_tree import wait_for_instance_in_status, tail_log_until_uvicorn_ready, prepare_component_tree, launch_component_instance
from mesh import get_component_status
import time

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")

import time
import json
from pathlib import Path

import time



@app.command()
def add(component: str):
    """
    Deploy new instance of a component to its tier environment.
    """
    # 1) Подготовка дерева компонентов: локального или git-based
    work_dir, manifest_path = prepare_component_tree(component)
    # 2) Запуск компонента
    launch_component_instance(work_dir, manifest_path)

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

    filtered = get_component_status(component)

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
                if inputs:
                    arg_str = ", ".join(f"\"{k}\": {v}" for k, v in inputs.items())
                    typer.echo(f"http://{ip}:{port}/{ep} {{ {arg_str} }}")
                else:
                    typer.echo(f"http://{ip}:{port}/{ep}")
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
    else:
        json_data = {}

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
def log(id: str, follow: bool = typer.Option(False, "-f", "--follow", help="Follow the log output")):
    """
    Show log output of a specific component instance by ID.
    """
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
#    typer.echo(f"📄 Showing log: {log_file}\n{'─'*40}")

    try:
        with open(log_file, "r") as f:
            if follow:
                # Перемещаемся в конец файла
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        typer.echo(line, nl=False)
                    else:
                        time.sleep(0.2)
            else:
                # Однократный вывод
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

