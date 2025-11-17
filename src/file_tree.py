from pathlib import Path
import time
import typer


def prepare_component_tree(component: str):
    """
    Возвращает (work_dir, manifest_path).
    work_dir — каталог, где находится компонент (локальный или временный git clone).
    manifest_path — путь к mvp.yaml в этом work_dir.
    """

    import tempfile
    import subprocess
    from pathlib import Path
    import yaml
    import shutil

    orig_manifest_path = Path(component).expanduser().resolve()

    if not orig_manifest_path.exists():
        typer.echo(f"❌ Manifest not found: {orig_manifest_path}")
        raise typer.Exit(1)

    # Прочитаем YAML для анализа source:
    try:
        with open(orig_manifest_path, "r") as f:
            manifest = yaml.safe_load(f)
    except Exception as e:
        typer.echo(f"❌ Failed to parse manifest: {e}")
        raise typer.Exit(1)

    if not isinstance(manifest, dict):
        typer.echo("❌ Manifest must be a YAML dictionary")
        raise typer.Exit(1)

    # Если source отсутствует → работаем в локальной директории
    if "source" not in manifest:
        work_dir = orig_manifest_path.parent
        return work_dir, orig_manifest_path

    # Если source есть → git clone
    source = manifest["source"]
    if "url" not in source:
        typer.echo("❌ 'source' requires a 'url' field")
        raise typer.Exit(1)

    url = source["url"]
    commit = source.get("commit")

    # Создаём tmp-каталог
    tmp_dir = Path(tempfile.mkdtemp(prefix="mvp-src-"))

    typer.echo(f"📥 Cloning: {url}")
    subprocess.run(["git", "clone", url, str(tmp_dir)], check=True)

    if commit:
        typer.echo(f"📌 Checking out commit: {commit}")
        subprocess.run(["git", "-C", str(tmp_dir), "checkout", commit], check=True)

    # Кладём манифест внутрь дерева клона
    manifest_path_in_clone = tmp_dir / orig_manifest_path.name
    shutil.copy2(orig_manifest_path, manifest_path_in_clone)

    typer.echo(f"📦 Using temporary component tree at: {tmp_dir}")

    return tmp_dir, manifest_path_in_clone



def launch_component_instance(work_dir: Path, manifest_path: Path):
    """
    Запускает компонент, используя рабочий каталог work_dir и путь к манифесту.
    Это — перенос старой логики из mvp add.
    """

    import subprocess
    import uuid
    import yaml
    from pathlib import Path
    import sys

    base_dir = Path(__file__).parent.parent.resolve()

    # Повторная валидация YAML (как раньше)
    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    required_keys = ["name", "endpoints"]
    for key in required_keys:
        if key not in manifest:
            typer.echo(f"❌ Manifest missing required key: {key}")
            raise typer.Exit(1)

    if not isinstance(manifest["endpoints"], list):
        typer.echo("❌ 'endpoints' must be a list of strings")
        raise typer.Exit(1)

    if "start" in manifest and not isinstance(manifest["start"], list):
        typer.echo("❌ 'start' must be a list of strings")
        raise typer.Exit(1)

    # install requirements.txt (если есть)
    req_file = work_dir / "requirements.txt"
    if req_file.exists():
        typer.echo("📦 Installing dependencies...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)

    # Готовим логи
    unique_id = uuid.uuid4().hex
    log_dir = Path.home() / ".mvp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp_log = log_dir / f"mvp-{unique_id}.tmp"

    # Запуск autorouter
    with open(tmp_log, "ab") as out:
        process = subprocess.Popen(
            [sys.executable, "-u", str(base_dir / "src" / "autorouter.py"), str(manifest_path), unique_id],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True
        )

    # Ожидание готовности uvicorn
    tail_log_until_uvicorn_ready(tmp_log)

    pid = process.pid
    final_log = log_dir / f"mvp-{unique_id}-pid{pid}.log"
    tmp_log.rename(final_log)

    # Ждем пока статус появится
    instance = wait_for_instance_in_status(unique_id)

    # Конверсия io → читаемые endpoint строки
    endpoint_strings = []
    for ep in instance["endpoints"]:
        sig = instance.get("io", {}).get(ep, {}).get("inputs", {})
        formatted = ", ".join(f"{k}: {v}" for k, v in sig.items())
        endpoint_strings.append(f"{ep} {{{formatted}}}")

    endpoint_strings.extend([ "intro/manifest", "output/log", "output/stream" ])
    endpoint_strings.sort()
    
    # Добавляем в статус
    add_instance(
        instance['name'],
        unique_id,
        instance['description'],
        f"http://{instance['ip']}:{instance['port']}",
        endpoint_strings
    )

    typer.echo(f"🚀 instance {unique_id} of {instance['name']} launched")



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

