import os
import sys
import signal
import subprocess
import time
import json
from typing import List, Optional, Any, Union
from pathlib import Path
import requests
import typer

from .file_tree import (
    wait_for_instance_in_status,
    tail_log_until_uvicorn_ready,
    prepare_component_tree,
    launch_component_instance
)
from .mesh import get_component_status

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")


# ==========================================
# CORE PROGRAMMATIC API (Для импорта в Python)
# ==========================================

def add(component: str) -> str:
    """
    Программный аналог CLI команды add.
    Развертывает компонент и возвращает его ID.
    """
    status_path = Path.home() / ".mvp" / "status"
    existing_ids = set()
    if status_path.exists():
        try:
            with open(status_path, "r") as f:
                existing_ids = {s.get("id") for s in json.load(f)}
        except Exception:
            pass

    # 1) Подготовка дерева компонентов
    work_dir, manifest_path = prepare_component_tree(component)
    
    # Пытаемся извлечь имя из манифеста, если оно там есть, для фоллбэка
    component_name = component
    try:
        import yaml
        with open(manifest_path, "r") as mf:
            m_data = yaml.safe_load(mf)
            if isinstance(m_data, dict):
                component_name = m_data.get("title", m_data.get("name", component_name))
    except Exception:
        pass

    # 2) Запуск компонента
    new_id = launch_component_instance(work_dir, manifest_path)

    # Небольшая пауза на регистрацию
    time.sleep(1.5)

    return new_id



def ls(component: Optional[str] = None):
    """Возвращает список статусов компонентов."""
    return get_component_status(component)



def contract(target: str) -> Any:
    """Запрашивает контракт напрямую у запущенного компонента по его ID/имени."""
    return call(target, "contract", {})


def register(manager_id: str, component_path: str) -> str:
    # 1. Запускаем компонент
    instance_id = add(component_path)

    # 2. Получаем текущий список компонентов из статус-файла
    instances = ls()
    # Находим наш инстанс
    instance = next((i for i in instances if i.get("id") == instance_id), None)

    # 3. Формируем base_url
    base_url = ""
    if instance and "port" in instance and "ip" in instance:
        base_url = f"http://{instance['ip']}:{instance['port']}"

    # 4. Снимаем контракт
    contract_data = contract(instance_id)

    # =========================================================
    # ВНЕДРЯЕМ INSTANCE ID ПРЯМО В ТЕЛО КОНТРАКТА
    # =========================================================
    if isinstance(contract_data, dict):
        contract_data["instance_id"] = instance_id

    # 5. Шлем в менеджер
    call(manager_id, "register", {
        "contract": contract_data,
        "base_url": base_url
    })

    return instance_id



# Замените функцию call() на эту:
def call(target: str, endpoint: str, data: Optional[Union[dict, list, str]] = None) -> Any:
    if data is None: data = {}
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists(): raise RuntimeError("❌ Status file not found.")

    with open(status_path, "r") as f:
        status = json.load(f)

    match = next((s for s in status if s.get("id") == target or s.get("name") == target), None)
    if not match: raise ValueError(f"❌ No instance found with ID or name '{target}'")

    port = match.get("port")
    if not port: raise RuntimeError(f"❌ No port found for instance '{target}'")

    url = f"http://127.0.0.1:{port}/{endpoint}"

    try:
        resp = requests.post(url, json=data)
        
        # Перехват заголовка Mock Tier
        if resp.headers.get("X-MVP-Mock-Fallback") == "true":
            err_msg = resp.headers.get("X-MVP-Original-Error", "Unknown")
            typer.secho(
                f"WARNING: Mock tier is responding on endpoint {url}. Status of the original endpoint: {err_msg}",
                fg=typer.colors.YELLOW, 
                err=True
            )

        resp.raise_for_status()
        try: return resp.json()
        except json.JSONDecodeError: return resp.text
    except Exception as e:
        raise RuntimeError(f"❌ Request failed: {e}")

# Обновите функцию rm и добавьте функцию purge:
def purge(instance: str):
    """Полностью убивает процесс Шлюза и стирает статус (Старая логика rm)"""
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists(): raise RuntimeError("status file not found")
    try:
        with open(status_path, "r") as f: status = json.load(f)
    except: raise RuntimeError(f"Failed to load status file")

    target_entry = next((e for e in status if e.get("id") == instance), None)
    if not target_entry: raise RuntimeError(f"Instance {instance} not found")

    try:
        ps_out = subprocess.check_output(["ps", "aux"], text=True)
        for line in ps_out.splitlines():
            if instance in line and "mvp.autorouter" in line:
                pid = int(line.split()[1])
                os.kill(pid, signal.SIGTERM)
                break
    except Exception as e:
        print(f"DEBUG ERROR in process cleanup: {e}")

    status = [entry for entry in status if entry.get("id") != instance]
    with open(status_path, "w") as f: json.dump(status, f, indent=2)

def rm(instance: str):
    """Отключает Воркер, оставляя работать Шлюз в режиме Mock"""
    try:
        call(instance, "_sys/rm")
    except Exception as e:
        raise RuntimeError(f"Failed to switch {instance} to Mock Tier: {e}")

# Замените команды CLI внизу файла:
@app.command(name="rm")
def cli_rm(instance: str):
    """Stop the actual code worker (Mock Tier takes over)."""
    try:
        rm(instance)
        typer.echo(f"Worker for {instance} stopped. Mock Tier is now active.")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)

@app.command(name="purge")
def cli_purge(instance: str):
    """Kill the component entirely (stops Gateway & removes from registry)."""
    try:
        purge(instance)
        typer.echo(f"Instance {instance} completely removed from MVP registry.")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)

@app.command(name="update")
def cli_update(instance: str):
    """Git pull and restart the component worker (Mock Tier active during reload)."""
    try:
        call(instance, "_sys/update")
        typer.echo(f"Instance {instance} successfully updated and restarted.")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)

# В функции unregister не забудьте заменить вызов rm на purge:
def unregister(manager_id: str, instance_id: str) -> Any:
    """
    Убирает компонент из реестра конкретного менеджера,
    после чего удаляет и останавливает сам инстанс компонента.
    """
    # 1. Определяем title компонента из контракта или статуса
    comp_title = None
    try:
        c_data = contract(instance_id)
        if isinstance(c_data, dict):
            comp_title = c_data.get("title") or c_data.get("info", {}).get("title")
    except Exception:
        pass

    if not comp_title:
        instances = ls()
        if isinstance(instances, list):
            for entry in instances:
                if entry.get("id") == instance_id:
                    comp_title = entry.get("title") or entry.get("name")
                    break

    # 2. Снимаем регистрацию в менеджере по title
    unreg_res = None
    if comp_title:
        try:
            unreg_res = call(manager_id, "unregister", {"title": comp_title})
        except Exception as e:
            print(f"Warning: Failed to unregister '{comp_title}' from manager: {e}")

    # 3. Останавливаем процесс и удаляем запись
    rm(instance_id)
    purge(instance_id)
    return unreg_res or f"Instance {instance_id} unregistered and removed"



def syslog(target: str, follow: bool = False) -> str:
    """
    Программный API для получения системного лога компонента через Шлюз.
    Если follow=True, функция будет непрерывно печатать новые строки в stdout.
    Если follow=False, вернет весь лог целиком в виде строки.
    """
    if not follow:
        # Переиспользуем нашу готовую функцию call,
        # она умеет возвращать raw text (resp.text), если ответ не JSON
        return call(target, "syslog")
    else:
        # Для стриминга (follow=True) используем requests с параметром stream
        status_path = Path.home() / ".mvp" / "status"
        if not status_path.exists():
            raise RuntimeError("❌ Status file not found.")

        with open(status_path, "r") as f:
            status = json.load(f)

        match = next((s for s in status if s.get("id") == target or s.get("name") == target), None)
        if not match:
            raise ValueError(f"❌ No instance found with ID or name '{target}'")

        port = match.get("port")
        url = f"http://127.0.0.1:{port}/syslog-stream"

        try:
            # Открываем поток и читаем его по строкам
            with requests.post(url, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        print(line.decode('utf-8'))
        except KeyboardInterrupt:
            # Корректно обрабатываем Ctrl+C (прерывание стриминга юзером)
            pass
        except Exception as e:
            raise RuntimeError(f"❌ Log streaming failed: {e}")

        return ""



# ==========================================
# TYPER CLI INTERFACE (Для терминала)
# ==========================================

@app.command(name="add")
def cli_add(component: str):
    """Deploy new instance of a component to its tier environment."""
    res = add(component)
    typer.echo(f"✅ Component deployed successfully. Target identifier: {res}")



@app.command(name="ls")
def cli_ls(component: Optional[str] = None):
    """Show status of a specific component or all deployed instances."""
    filtered = ls(component)
    if not filtered:
        if component:
            typer.echo(f"no instances found for component: {component}")
        else:
            typer.echo("MVP registry is empty")
        raise typer.Exit()

    for entry in filtered:
        typer.echo("────────────────────────────────────────")
        typer.echo(f"Component    {entry.get('title', entry.get('name', '[unknown]'))}")
        typer.echo(f"Instance     {entry.get('id', '[unknown]')}")
        typer.echo(f"Subtitle     {entry.get('subtitle', entry.get('description', ''))}")

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


@app.command(name="call")
def cli_call(
    target: str,
    endpoint: str,
    json_parts: Optional[List[str]] = typer.Argument(None, help="Optional JSON payload split into parts")
):
    """Call an endpoint on a deployed instance, optionally passing JSON input."""
    json_data = {}
    if json_parts:
        json_str = " ".join(json_parts)
        try:
            json_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ Invalid JSON: {e}")
            raise typer.Exit(1)

    try:
        result = call(target, endpoint, json_data)
        if isinstance(result, (dict, list)):
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(result))
    except Exception as e:
        typer.echo(str(e))
        raise typer.Exit(1)


@app.command(name="contract")
def cli_contract(target: str):
    """Fetch contract JSON from a deployed component instance."""
    try:
        res = contract(target)
        if isinstance(res, (dict, list)):
            typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(res))
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="register")
def cli_register(
    manager_id: str = typer.Argument(..., help="Instance ID of the target Manager"),
    component_path: str = typer.Argument(..., help="Path to the component .yaml file")
):
    """Deploy component, fetch its contract, register it in manager, and return instance ID."""
    try:
        instance_id = register(manager_id, component_path)
        typer.echo(instance_id)
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)



@app.command()
def log(id: str, follow: bool = typer.Option(False, "-f", "--follow", help="Follow the log output")):
    """Show log output of a specific component instance by ID."""
    log_dir = Path.home() / ".mvp" / "logs"
    if not log_dir.exists():
        raise typer.Exit(1)

    matching_logs = list(log_dir.glob(f"mvp-{id}-pid*.log"))
    if not matching_logs:
        typer.echo(f"❌ No log file found for ID {id}")
        raise typer.Exit(1)

    log_file = matching_logs[0]
    try:
        with open(log_file, "r") as f:
            if follow:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        typer.echo(line, nl=False)
                    else:
                        time.sleep(0.2)
            else:
                typer.echo(f.read())
    except Exception as e:
        typer.echo(f"❌ Failed to read log file: {e}")
        raise typer.Exit(1)


@app.command()
def switch(component: str, tier: str = typer.Argument(..., help="Target tier: mock | prod | preprod")):
    """Switch component to given tier."""
    if tier not in {"mock", "prod", "preprod"}:
        typer.echo("❌ Error: tier must be one of: mock, prod, preprod")
        raise typer.Exit(1)
    typer.echo(f"🔄 Switching {component} to tier: {tier}")


@app.command()
def ask(question: str):
    """Ask the AI assistant about available components or their functionality."""
    typer.echo(f"🤖 Asking AI: {question}")

@app.command(name="syslog")
def cli_syslog(
    target: str, 
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs continuously")
):
    """
    Fetch logs from the component's syslog endpoint via API.
    """
    try:
        if follow:
            syslog(target, follow=True)
        else:
            logs = syslog(target, follow=False)
            # Выводим через обычный print, чтобы сохранить 
            # оригинальные переносы строк и форматирование лога
            print(logs, end="")
    except Exception as e:
        typer.secho(f"❌ Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)



if __name__ == "__main__":
    app()
