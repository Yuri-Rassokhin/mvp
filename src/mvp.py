from pathlib import Path
import typer
import subprocess
import sys

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

    # Prepare nohup log file
    log_file = Path.home() / ".mvp" / f"{manifest_path.stem}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as out:
        subprocess.Popen(
            [sys.executable, str(base_dir / "src" / "autorouter.py"), str(manifest_path)],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True  # fully detaches from SSH/tty
        )

    typer.echo(f"component {manifest_path.stem} has launched, logs in {log_file}")

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

