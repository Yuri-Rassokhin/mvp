from pathlib import Path
import time
import typer

def prepare_component_tree(component: str):
    import tempfile
    import subprocess
    from pathlib import Path
    import yaml
    import shutil

    orig_manifest_path = Path(component).expanduser().resolve()

    if not orig_manifest_path.exists():
        typer.echo(f"ERROR: Contract {orig_manifest_path} not found")
        raise typer.Exit(1)

    try:
        with open(orig_manifest_path, "r") as f:
            manifest = yaml.safe_load(f)
    except Exception as e:
        typer.echo(f"ERROR: Failed to parse contract: {e}")
        raise typer.Exit(1)

    if not isinstance(manifest, dict):
        typer.echo("ERROR: Contract must be in YAML format")
        raise typer.Exit(1)

    if "source" not in manifest:
        work_dir = orig_manifest_path.parent
        return work_dir, orig_manifest_path

    source = manifest["source"]
    if "url" not in source:
        typer.echo("ERROR: 'source' requires a 'url' field")
        raise typer.Exit(1)

    url = source["url"]
    commit = source.get("commit")

    tmp_dir = Path(tempfile.mkdtemp(prefix="mvp-src-"))

    typer.echo(f"INFO: Cloning URL {url}")
    subprocess.run(["git", "clone", url, str(tmp_dir)], check=True)

    if commit:
        typer.echo(f"INFO: Checking out commit {commit}")
        subprocess.run(["git", "-C", str(tmp_dir), "checkout", commit], check=True)

    manifest_path_in_clone = tmp_dir / orig_manifest_path.name
    shutil.copy2(orig_manifest_path, manifest_path_in_clone)

    typer.echo(f"INFO: Using temporary component tree at: {tmp_dir}")
    return tmp_dir, manifest_path_in_clone

def launch_component_instance(work_dir: Path, manifest_path: Path):
    import subprocess
    import uuid
    import yaml
    from pathlib import Path
    import sys

    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    if "title" not in manifest and "name" not in manifest:
        typer.echo("ERROR: contract missing required key: 'title' (or 'name')")
        raise typer.Exit(1)
        
    if "endpoints" not in manifest:
        typer.echo("ERROR: contract missing required key: 'endpoints'")
        raise typer.Exit(1)

    if not isinstance(manifest["endpoints"], (list, dict)):
        typer.echo("ERROR: 'endpoints' must be a list or a dictionary")
        raise typer.Exit(1)

    if "start" in manifest and not isinstance(manifest["start"], list):
        typer.echo("ERROR: 'start' must be a list of strings")
        raise typer.Exit(1)

    req_file = work_dir / "requirements.txt"
    if req_file.exists():
        typer.echo("INFO: Installing dependencies...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)

    unique_id = uuid.uuid4().hex
    log_dir = Path.home() / ".mvp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp_log = log_dir / f"mvp-{unique_id}.tmp"

    with open(tmp_log, "ab") as out:
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", "mvp.autorouter", str(manifest_path), unique_id],
                stdout=out,
                stderr=out,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True
            )    

    # Waiting server to report its status in logs
    tail_log_until_uvicorn_ready(tmp_log)

    pid = process.pid
    final_log = log_dir / f"mvp-{unique_id}-pid{pid}.log"
    tmp_log.rename(final_log)

    instance_name = manifest.get('title', manifest.get('name', 'unknown'))
    typer.echo(f"INFO: Instance {unique_id} of '{instance_name}' launched")
    return unique_id

def tail_log_until_uvicorn_ready(log_path: Path, timeout: int = 180, poll_interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    seen_uvicorn = False
    position = 0

    print("INFO: Launching MVP server")

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
            print("INFO: Endpoint server launched successfully")
            return True

        time.sleep(poll_interval)

    print("WARN: Server startup timeout, continuing anyway")
    return False
