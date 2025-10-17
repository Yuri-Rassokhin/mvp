from pathlib import Path
import typer
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
        typer.echo(f"❌ manifest {manifest_path} not found")
        raise typer.Exit(1)

    # Install dependencies if requirements.txt is present
    req_file = base_dir / "requirements.txt"
    if req_file.exists():
        typer.echo("📦 installing dependencies")
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
    final_log_path = log_dir / f"{component_name}-{unique_id}--pid{pid}.log"
    tmp_log_path.rename(final_log_path)

    typer.echo(f"🚀 Component {component_name} launched (PID: {pid})")
    typer.echo(f"📂 Logs: {final_log_path}")

@app.command()
def rm(component: str):
    """
    Delete a component from the mesh (removes it from status file).
    """
    from pathlib import Path
    import json

    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        typer.echo("Status file not found.")
        raise typer.Exit(1)

    try:
        with open(status_path, "r") as f:
            status = json.load(f)
    except Exception as e:
        typer.echo(f"❌ Failed to load status file: {e}")
        raise typer.Exit(1)

    original_len = len(status)
    status = [entry for entry in status if entry.get("name") != component]

    if len(status) == original_len:
        typer.echo(f"Component '{component}' not found")
    else:
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)
        typer.echo(f"Component '{component}' removed from MVP registry")

@app.command()
@app.command()
def status(component: Optional[str] = None):
    """
    Show status of a specific component or all deployed instances.
    """
    import json
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        typer.echo("ℹ️  No components deployed yet.")
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
            typer.echo(f"❌ No instances found for component: {component}")
        else:
            typer.echo("ℹ️  No component instances found.")
        raise typer.Exit()

    for entry in filtered:
        typer.echo("────────────────────────────────────────")
        typer.echo(f"🆔 ID:         {entry.get('id', '[unknown]')}")
        typer.echo(f"🔧 Name:       {entry.get('name', '[unknown]')}")
        typer.echo(f"📝 Desc:       {entry.get('description', '')}")
        typer.echo(f"📡 Endpoints:  {', '.join(entry.get('endpoints', []))}")
        typer.echo(f"🌐 IP:         {entry.get('ip', '')}")
        typer.echo(f"🚪 Port:       {entry.get('port', '')}")
    typer.echo("────────────────────────────────────────")

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

