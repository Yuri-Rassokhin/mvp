from pathlib import Path
import typer
import subprocess
import sys
import uuid

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")

@app.command()
def apply(component: str):
    """
    Apply (deploy or update) a component to its tier environment.
    """
    typer.echo(f"applying manifest {component}")

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
            [sys.executable, str(base_dir / "src" / "autorouter.py"), str(manifest_path)],
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
def status(component: str):
    """
    Show status of the component.
    """
    typer.echo(f"🔍 Checking status for component: {component}")
    # TODO: check current tier, process status, logs

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

