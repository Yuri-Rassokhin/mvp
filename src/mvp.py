from pathlib import Path
import typer
import os
import signal
import subprocess
import sys
import uuid
import json
from rich.table import Table
from rich.console import Console
from typing import Optional

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")

@app.command()
def add(component: str):
    """
    Deploy new instance of a component to its tier environment.
    """
    typer.echo(f"building component from manifest {component}")

    base_dir = Path(__file__).parent.parent.resolve()
    manifest_path = base_dir / f"{component}"

    if not manifest_path.exists():
        typer.echo(f"manifest {manifest_path} not found")
        raise typer.Exit(1)

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
    log_file_base = log_dir / f"{component_name}-{unique_id}"
    tmp_log_path = log_file_base.with_suffix(".tmp")  # временный лог, до получения PID

    with open(tmp_log_path, "ab") as out:
        process = subprocess.Popen(
            [sys.executable, str(base_dir / "src" / "autorouter.py"), str(manifest_path), unique_id],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True
        )

    pid = process.pid
    final_log_path = log_dir / f"{component_name}-{unique_id}-pid{pid}.log"
    tmp_log_path.rename(final_log_path)

    typer.echo(f"component {component_name} launched, log {final_log_path}")

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
        typer.echo(f"instance {instance} stopped and removed from MVP registry")

@app.command()
def status(component: Optional[str] = None):
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

    if endpoints:
        io = entry.get("io", {})
        for ep in endpoints:
            sig = io.get(ep, {})
            inputs = sig.get("inputs", {})
            output = sig.get("output", "unknown")

            arg_str = ", ".join(f"{k}: {v}" for k, v in inputs.items()) if inputs else ""
            typer.echo(f"http://{ip}:{port}/{ep}  {arg_str} → {output}")
    else:
        typer.echo("URL not found")

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

