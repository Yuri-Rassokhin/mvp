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
import httpx
import asyncio
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.console import Group
from rich.rule import Rule

from .file_tree import (
    tail_log_until_uvicorn_ready,
    prepare_component_tree,
    launch_component_instance
)
from .gossip import MESH_PROTOCOL_VERSION, PORT_RANGE

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")


# ==========================================
# CORE PROGRAMMATIC API (Для импорта в Python)
# ==========================================

def add(component: str) -> str:
    """
    Программный аналог CLI команды add.
    Развертывает компонент и возвращает его ID.
    """
    work_dir, manifest_path = prepare_component_tree(component)
    new_id = launch_component_instance(work_dir, manifest_path)
    time.sleep(1.5)
    return new_id


def contract(target: str) -> Any:
    """Запрашивает контракт напрямую у запущенного компонента по его ID/имени."""
    return call(target, "contract", {})


def register(manager_id: str, component_path: str) -> str:
    # 1. Запускаем компонент
    instance_id = add(component_path)
    # Ждем пару секунд, чтобы компонент успел зайти в Gossip Mesh
    time.sleep(2.0)

    # 2. Ищем base_url через Mesh
    port, contract_data = asyncio.run(fetch_global_contract())
    base_url = ""
    if contract_data:
        modules = contract_data.get("modules", [])
        match = next((m for m in modules if m.get("instance_id") == instance_id), None)
        if match:
            base_url = match.get("base_url", "")

    # 3. Снимаем контракт
    c_data = contract(instance_id)

    # 4. ВНЕДРЯЕМ INSTANCE ID
    if isinstance(c_data, dict):
        c_data["instance_id"] = instance_id

    # 5. Шлем в менеджер
    call(manager_id, "register", {
        "contract": c_data,
        "base_url": base_url
    })

    return instance_id


def call(target: str, endpoint: str, data: Optional[Union[dict, list, str]] = None) -> Any:
    if data is None: data = {}
    
    # Запрашиваем глобальный контракт через Gossip Mesh
    port, contract_data = asyncio.run(fetch_global_contract())
    if not contract_data:
        raise RuntimeError("❌ MVP Framework mesh is empty or unreachable.")

    modules = contract_data.get("modules", [])
    match = next((m for m in modules if m.get("instance_id") == target or m.get("title") == target), None)

    if not match: 
        raise ValueError(f"❌ No instance found with ID or name '{target}'")

    base_url = match.get("base_url")
    if not base_url: 
        raise RuntimeError(f"❌ No base_url found for instance '{target}'")

    url = f"{base_url}/{endpoint.lstrip('/')}"

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
    except requests.HTTPError as e:
        response = e.response
        detail = ""
        if response is not None:
            try:
                payload = response.json()
                body = payload.get("detail", payload) if isinstance(payload, dict) else payload
                detail = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
            except ValueError:
                detail = response.text
        suffix = f"\nResponse: {detail[:4096]}" if detail else ""
        raise RuntimeError(f"❌ Request failed: {e}{suffix}") from e
    except Exception as e:
        raise RuntimeError(f"❌ Request failed: {e}") from e


def purge(instance: str):
    """Полностью убивает процесс Шлюза"""
    try:
        ps_out = subprocess.check_output(["ps", "aux"], text=True)
        for line in ps_out.splitlines():
            if instance in line and "mvp.autorouter" in line:
                pid = int(line.split()[1])
                os.kill(pid, signal.SIGTERM)
                break
    except Exception as e:
        print(f"DEBUG ERROR in process cleanup: {e}")


def rm(instance: str):
    """Отключает Воркер, оставляя работать Шлюз в режиме Mock"""
    try:
        call(instance, "_sys/rm")
    except Exception as e:
        raise RuntimeError(f"Failed to switch {instance} to Mock Tier: {e}")


@app.command(name="rm")
def cli_rm(instance: str):
    """Stop the actual code worker (Mock Tier takes over)."""
    try:
        rm(instance)
        typer.echo(f"Instance {instance} REMOVED from MVP Framework")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="purge")
def cli_purge(instance: str):
    """Kill the component entirely (stops Gateway & removes from registry)."""
    try:
        purge(instance)
        typer.echo(f"Instance {instance} PURGED from MVP Framework")
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


def unregister(manager_id: str, instance_id: str) -> Any:
    """
    Убирает компонент из реестра конкретного менеджера,
    после чего удаляет и останавливает сам инстанс компонента.
    """
    comp_title = None
    try:
        c_data = contract(instance_id)
        if isinstance(c_data, dict):
            comp_title = c_data.get("title") or c_data.get("info", {}).get("title")
    except Exception:
        pass

    if not comp_title:
        port, contract_data = asyncio.run(fetch_global_contract())
        if contract_data:
            modules = contract_data.get("modules", [])
            for entry in modules:
                if entry.get("instance_id") == instance_id:
                    comp_title = entry.get("title")
                    break

    unreg_res = None
    if comp_title:
        try:
            unreg_res = call(manager_id, "unregister", {"title": comp_title})
        except Exception as e:
            print(f"Warning: Failed to unregister '{comp_title}' from manager: {e}")

    rm(instance_id)
    purge(instance_id)
    return unreg_res or f"Instance {instance_id} unregistered and removed"


def syslog(target: str, follow: bool = False) -> str:
    """
    Программный API для получения системного лога компонента через Шлюз.
    """
    if not follow:
        return call(target, "syslog")
    else:
        # Для стриминга через Gossip находим базу URL
        port, contract_data = asyncio.run(fetch_global_contract())
        if not contract_data:
            raise RuntimeError("❌ MVP Framework mesh is empty or unreachable.")

        modules = contract_data.get("modules", [])
        match = next((m for m in modules if m.get("instance_id") == target or m.get("title") == target), None)

        if not match:
            raise ValueError(f"❌ No instance found with ID or name '{target}'")

        base_url = match.get("base_url")
        url = f"{base_url}/syslog-stream"

        try:
            with requests.post(url, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        print(line.decode('utf-8'))
        except KeyboardInterrupt:
            pass
        except Exception as e:
            raise RuntimeError(f"❌ Log streaming failed: {e}")

        return ""


console = Console()

async def fetch_global_contract():
    headers = {"X-Mesh-Version": MESH_PROTOCOL_VERSION}
    async with httpx.AsyncClient(timeout=0.5) as client:
        for port in range(PORT_RANGE[0], PORT_RANGE[1]):
            try:
                probe = await client.post(
                    f"http://127.0.0.1:{port}/_gossip",
                    headers=headers,
                    json={"sender_id": "cli", "registry": {}}
                )
                if probe.status_code == 200:
                    openapi_resp = await client.get(
                        f"http://127.0.0.1:{port}/openapi",
                        headers=headers,
                        timeout=2.0
                    )
                    if openapi_resp.status_code == 200:
                        return port, openapi_resp.json()
            except Exception:
                continue
    return None, None

def resolve_schema_type(schema_ref: dict, components: dict) -> str:
    """Извлекает и форматирует структуру из Pydantic/OpenAPI схем"""
    if not schema_ref:
        return "None"

    if "$ref" in schema_ref:
        schema_name = schema_ref["$ref"].split("/")[-1]
        schema = components.get("schemas", {}).get(schema_name, {})
        props = schema.get("properties", {})

        sig_parts = []
        for prop_name, prop_details in props.items():
            prop_type = prop_details.get("type", "Any")
            if prop_type == "array":
                prop_type = "List"
            sig_parts.append(f'"{prop_name}": {prop_type}')

        return f"{{ {', '.join(sig_parts)} }}"

    return str(schema_ref.get("type", "Unknown"))

async def async_ls(component_filter: Optional[str] = None):
    with console.status("[bold green]Scanning Gossip Mesh for active modules...", spinner="dots"):
        port, contract_data = await fetch_global_contract()

    if not contract_data:
        console.print("[yellow]MVP Framework is empty[/yellow]")
        return

    console.print(f"[dim]Connected to MVP Framework at port {port}[/dim]\n")

    modules = contract_data.get("modules", [])
    components = contract_data.get("components", {})

    for module in modules:
        title = module.get("title", "Unknown Module")
        instance_id = module.get("instance_id", "unknown")

        if component_filter and component_filter.lower() not in title.lower() and component_filter != instance_id:
            continue

        subtitle = module.get("subtitle", "")
        base_url = module.get("base_url", "")

        header = Text()
        header.append("Component    ", style="dim")
        header.append(f"{title}\n", style="bold cyan")
        header.append("Instance     ", style="dim")
        header.append(f"{instance_id}\n", style="yellow")
        header.append("Subtitle     ", style="dim")
        header.append(f"{subtitle}\n", style="italic")

        endpoints_text = Text()
        endpoints = module.get("endpoints", {})

        for path, methods in endpoints.items():
            for method, details in methods.items():
                full_url = f"{base_url}{path}"
                req_schema = details.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
                req_sig = resolve_schema_type(req_schema, components)
                resp_schema = details.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
                resp_type = resolve_schema_type(resp_schema, components)

                if path in ["/contract", "/syslog", "/syslog-stream"]:
                    endpoints_text.append(f"GET {full_url}\n", style="dim")
                else:
                    comment = details.get("x-visual-comment") or details.get("summary")
                    if comment:
                        endpoints_text.append(f"# {comment}\n", style="dim italic")

                    endpoints_text.append(f"{method.upper()} ", style="bold green")
                    endpoints_text.append(f"{full_url} ")
                    endpoints_text.append(f"{req_sig} ", style="yellow")
                    endpoints_text.append(f"-> {resp_type}\n\n", style="blue") 

        panel_content = Group(header, endpoints_text)
        console.print(Panel(panel_content, expand=True, border_style="blue"))
        console.print()

@app.command(name="ls")
def ls(component: Optional[str] = None):
    """Lists all active modules dynamically from the Gossip network."""
    asyncio.run(async_ls(component_filter=component))


# ==========================================
# TYPER CLI INTERFACE (Для терминала)
# ==========================================

@app.command(name="add")
def cli_add(component: str):
    """Deploy new instance of a component to its tier environment."""
    res = add(component)
    typer.echo(f" Component '{component}' deployed successfully, instance ID {res}")


@app.command(name="ls")
def cli_ls(component: Optional[str] = None):
    """Show status of a specific component or all deployed instances."""
    # async_ls теперь отвечает за всю красоту вывода
    ls(component)


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
            print(logs, end="")
    except Exception as e:
        typer.secho(f"❌ Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

if __name__ == "__main__":
    app()
