import json
from pathlib import Path
import shutil
import time
from typing import Optional, Dict, Any
import typer

from .config import (
    DEFAULT_PROD_BRANCH,
    FILE_TREE_STARTUP_POLL_INTERVAL_SEC,
    FILE_TREE_STARTUP_TIMEOUT_SEC,
    LOG_DIR,
    RUN_DIR,
    TEMP_DIR_MARKER,
    TMP_DIR_BASE,
    TMP_SRC_PREFIX,
)


def is_temp_work_dir(work_dir: Optional[Path]) -> bool:
    """Проверяет наличие маркера временного клона внутри рабочей директории."""
    if not work_dir:
        return False
    try:
        resolved = Path(work_dir).resolve()
        return resolved.exists() and (resolved / TEMP_DIR_MARKER).is_file()
    except Exception:
        return False


def cleanup_temp_work_dir(work_dir: Optional[Path]) -> bool:
    """Безопасно удаляет временную директорию клона (только при наличии маркера .mvp_temp_dir)."""
    if is_temp_work_dir(work_dir):
        shutil.rmtree(Path(work_dir).resolve(), ignore_errors=True)
        return True
    return False


def write_instance_run_metadata(
    instance_id: str,
    pid: int,
    work_dir: Path,
    manifest_path: Path,
) -> Path:
    """Сохраняет PID и пути инстанса в RUN_DIR для точного завершения без ps aux и очистки tmp."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUN_DIR / f"mvp-{instance_id}.json"
    payload = {
        "instance_id": instance_id,
        "pid": pid,
        "work_dir": str(work_dir.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "is_temp_dir": is_temp_work_dir(work_dir),
        "created_at": time.time(),
    }
    run_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return run_file


def read_instance_run_metadata(instance_id: str) -> Optional[Dict[str, Any]]:
    run_file = RUN_DIR / f"mvp-{instance_id}.json"
    if not run_file.exists():
        return None
    try:
        return json.loads(run_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def cleanup_instance_resources(instance_id: str, work_dir: Optional[Path] = None) -> None:
    """Удаляет временную директорию клона и файл метаданных инстанса при остановке/purge."""
    meta = read_instance_run_metadata(instance_id)
    target_dir = Path(meta["work_dir"]) if meta and meta.get("work_dir") else work_dir
    cleanup_temp_work_dir(target_dir)

    run_file = RUN_DIR / f"mvp-{instance_id}.json"
    if run_file.exists():
        try:
            run_file.unlink()
        except OSError:
            pass


def prepare_component_tree(component: str):
    import tempfile
    import subprocess
    import yaml

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
    branch = source.get("branch", DEFAULT_PROD_BRANCH)  # Читаем ветку

    tmp_dir = Path(tempfile.mkdtemp(prefix=TMP_SRC_PREFIX, dir=TMP_DIR_BASE))
    try:
        typer.echo(f"INFO: Cloning URL {url} (branch: {branch})")
        subprocess.run(["git", "clone", "-b", branch, url, str(tmp_dir)], check=True)

        if commit:
            typer.echo(f"INFO: Checking out commit {commit}")
            subprocess.run(["git", "-C", str(tmp_dir), "checkout", commit], check=True)

        # Помечаем директорию маркером временного клона для последующей автоочистки
        (tmp_dir / TEMP_DIR_MARKER).write_text(url, encoding="utf-8")

        manifest_path_in_clone = tmp_dir / orig_manifest_path.name
        shutil.copy2(orig_manifest_path, manifest_path_in_clone)

        typer.echo(f"INFO: Using temporary component tree at: {tmp_dir}")
        return tmp_dir, manifest_path_in_clone
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def launch_component_instance(work_dir: Path, manifest_path: Path):
    import subprocess
    import uuid
    import yaml
    import sys

    try:
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
        log_dir = LOG_DIR
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
    except BaseException:
        cleanup_temp_work_dir(work_dir)
        raise

    pid = process.pid
    write_instance_run_metadata(
        instance_id=unique_id,
        pid=pid,
        work_dir=work_dir,
        manifest_path=manifest_path,
    )

    # Waiting server to report its status in logs
    tail_log_until_uvicorn_ready(tmp_log)

    final_log = log_dir / f"mvp-{unique_id}-pid{pid}.log"
    tmp_log.rename(final_log)

    instance_name = manifest.get('title', manifest.get('name', 'unknown'))
    typer.echo(f"INFO: Instance {unique_id} of '{instance_name}' launched")
    return unique_id


def tail_log_until_uvicorn_ready(
    log_path: Path,
    timeout: int = FILE_TREE_STARTUP_TIMEOUT_SEC,
    poll_interval: float = FILE_TREE_STARTUP_POLL_INTERVAL_SEC,
) -> bool:
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
