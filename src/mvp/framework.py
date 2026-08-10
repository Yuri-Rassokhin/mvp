import os
import sys
import pathlib
import signal
import subprocess
import time
import uuid
import json
from typing import List, Optional, Any, Union
from pathlib import Path
from rich.table import Table
from rich.console import Console
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
# HELPER FUNCTIONS
# ==========================================

def _find_manager_target() -> str:
    """Helper to find running manager instance name or ID from status registry."""
    try:
        instances = ls()
        if isinstance(instances, list):
            for entry in instances:
                name = str(entry.get("name", "")).lower()
                title = str(entry.get("title", "")).lower()
                if "manager" in name or "manager" in title:
                    return entry.get("id") or entry.get("name")
    except Exception:
        pass
    return "manager"


# ==========================================
# CORE PROGRAMMATIC API (Для импорта в Python)
# ==========================================

def add(component: str) -> str:
    """
    Программный аналог CLI команды add.
    Развертывает компонент и возвращает его ID или имя.
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


def call(target: str, endpoint: str, data: Optional[Union[dict, list, str]] = None) -> Any:
    """
    Программный аналог CLI команды call.
    Принимает python-объект (dict, list) или строку в качестве полезной нагрузки (payload),
    делает запрос к эндпоинту и возвращает разобранный JSON или текст ответа.
    """
    if data is None:
        data = {}

    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        raise RuntimeError("❌ Status file not found.")

    with open(status_path, "r") as f:
        status = json.load(f)

    # Ищем по ID или имени
    match = next((s for s in status if s.get("id") == target or s.get("name") == target), None)
    if not match:
        raise ValueError(f"❌ No instance found with ID or name '{target}'")

    port = match.get("port")
    if not port:
        raise RuntimeError(f"❌ No port found for instance '{target}'")

    url = f"http://127.0.0.1:{port}/{endpoint}"

    try:
        resp = requests.post(url, json=data)
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text
    except Exception as e:
        raise RuntimeError(f"❌ Request failed: {e}")


def ls(component: Optional[str] = None):
    """Возвращает список статусов компонентов."""
    return get_component_status(component)


def rm(instance: str):
    """Останавливает и удаляет инстанс."""
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        raise RuntimeError("status file not found")

    try:
        with open(status_path, "r") as f:
            status = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Failed to load status file: {e}")

    target_entry = next((entry for entry in status if entry.get("id") == instance), None)
    if not target_entry:
        raise RuntimeError(f"Instance {instance} not found in MVP registry, nothing to remove")

    try:
        ps_out = subprocess.check_output(["ps", "aux"], text=True)
        for line in ps_out.splitlines():
            if instance in line and "mvp.autorouter" in line:
                parts = line.split()
                pid = int(parts[1])
                os.kill(pid, signal.SIGTERM)
                break
    except Exception as e:
        print(f"DEBUG ERROR in process cleanup: {e}")

    status = [entry for entry in status if entry.get("id") != instance]
    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)


def contract(target: str) -> Any:
    """Запрашивает контракт напрямую у запущенного компонента по его ID/имени."""
    return call(target, "contract", {})


def register(manager_or_component_spec: str, component_spec: Optional[str] = None) -> Any:
    """
    Развертывает компонент (add), снимает с него контракт (contract)
    и передает менеджеру (register).

    Варианты вызова:
      mvp.register("path/to/contract.yaml")  # Менеджер находится автоматически
      mvp.register(manager, "path/to/contract.yaml")
    """
    if component_spec is None:
        target_path = manager_or_component_spec
        mgr_target = _find_manager_target()
    else:
        mgr_target = manager_or_component_spec
        target_path = component_spec

    # 1. Запускаем компонент через add
    instance_id = add(target_path)

    # 2. Снимаем с него контракт через contract
    contract_data = contract(instance_id)

    # 3. Передаем полученный контракт в менеджер
    reg_response = call(mgr_target, "register", {"contract": contract_data})

    # Прикрепляем instance_id к ответу для удобства последующего взаимодействия
    if isinstance(reg_response, dict):
        reg_response["instance_id"] = instance_id

    return reg_response


def unregister(manager_or_title: str, title: Optional[str] = None) -> Any:
    """
    Удаляет контракт компонента из реестра менеджера по его title.
    Компонент остается запущенным, но исключается из внешней кооперации.

    Варианты вызова:
      mvp.unregister("Ethics & Conduct Coach")  # Менеджер находится автоматически
      mvp.unregister(manager, "Ethics & Conduct Coach")
    """
    if title is None:
        comp_title = manager_or_title
        mgr_target = _find_manager_target()
    else:
        mgr_target = manager_or_title
        comp_title = title

    return call(mgr_target, "unregister", {"title": comp_title})


# ==========================================
# TYPER CLI INTERFACE (Для терминала)
# ==========================================

@app.command(name="add")
def cli_add(component: str):
    """Deploy new instance of a component to its tier environment."""
    res = add(component)
    typer.echo(f"✅ Component deployed successfully. Target identifier: {res}")


@app.command(name="rm")
def cli_rm(instance: str):
    """Stop a component and delete it from the registry."""
    try:
        rm(instance)
        typer.echo(f"instance {instance} stopped and removed from MVP registry")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


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
    manager_or_component_spec: str = typer.Argument(..., help="Path to component .yaml OR Manager target ID/name if manager is passed first"),
    component_spec: Optional[str] = typer.Argument(None, help="Path to component .yaml if manager was specified as first arg")
):
    """Deploy component, fetch its contract, and register it in the manager."""
    try:
        res = register(manager_or_component_spec, component_spec)
        if isinstance(res, (dict, list)):
            typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(res))
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="unregister")
def cli_unregister(
    manager_or_title: str = typer.Argument(..., help="Component title to unregister OR Manager target ID/name if manager is passed first"),
    title: Optional[str] = typer.Argument(None, help="Component title if manager was specified as first arg")
):
    """Unregister a component contract from the manager by title without stopping the process."""
    try:
        res = unregister(manager_or_title, title)
        if isinstance(res, (dict, list)):
            typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(res))
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


if __name__ == "__main__":
    app()

