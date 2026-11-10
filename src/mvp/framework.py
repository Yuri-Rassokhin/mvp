import errno
import os
import re
import sys
import signal
import subprocess
import time
import json
from typing import List, Optional, Any, Union, Dict
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

from .config import (
    API_KEY_HEADER_NAME,
    DISCOVERY_HOST,
    FRAMEWORK_ADD_SLEEP_SEC,
    FRAMEWORK_LOG_FOLLOW_SLEEP_SEC,
    FRAMEWORK_OPENAPI_TIMEOUT_SEC,
    FRAMEWORK_PROBE_TIMEOUT_SEC,
    FRAMEWORK_PURGE_POLL_INTERVAL_SEC,
    FRAMEWORK_PURGE_WAIT_TIMEOUT_SEC,
    LOG_DIR,
    MESH_PROTOCOL_VERSION,
    PORT_RANGE,
    SECRET_TOKEN,
    SEED_PEERS,
)
from .file_tree import (
    cleanup_instance_resources,
    launch_component_instance,
    prepare_component_tree,
    read_instance_run_metadata,
    tail_log_until_uvicorn_ready,
)
from .hooks import load_orchestrator_hook, slugify_module_name

_client_hook = load_orchestrator_hook()

app = typer.Typer(help="MVP CLI tool to manage lifecycle of a component mesh")


# ==========================================
# CORE PROGRAMMATIC API (Для импорта в Python)
# ==========================================

def add(component: str) -> str:
    work_dir, manifest_path = prepare_component_tree(component)
    new_id = launch_component_instance(work_dir, manifest_path)
    time.sleep(FRAMEWORK_ADD_SLEEP_SEC)
    return new_id

def contract(*, module: Optional[str] = None, instance: Optional[str] = None) -> Any:
    """Fetch contract JSON explicitly by module name or instance ID."""
    return call(endpoint="contract", module=module, instance=instance, data={})

def _resolve_target(modules: List[Dict[str, Any]], instance: Optional[str] = None, module: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Service Discovery & Replica Load-Balancing Hook.
    При поиске по instance возвращает конкретную реплику.
    При поиске по module (title или slug) балансирует вызовы между всеми живыми репликами через ReplicaOrchestratorHook.
    """
    if instance:
        return next((m for m in modules if m.get("instance_id") == instance), None)

    if module:
        target_slug = slugify_module_name(module)
        matched = [
            (m.get("instance_id", str(idx)), m)
            for idx, m in enumerate(modules)
            if m.get("title") == module
            or (m.get("module_slug") or slugify_module_name(m.get("title", ""))) == target_slug
        ]
        if not matched:
            return None
        _, chosen = _client_hook.select_replica(matched, key=f"sdk:{target_slug}")
        return chosen

    return None

def call(*, endpoint: str, module: Optional[str] = None, instance: Optional[str] = None, data: Optional[Union[dict, list, str]] = None, headers: Optional[dict] = None) -> Any:
    """
    Calls an endpoint on a deployed instance in the MVP Framework.
    ALL arguments must be passed as keyword arguments.
    You must provide EXACTLY ONE routing parameter: either `instance` or `module`.
    """
    if not instance and not module:
        raise ValueError("❌ Routing error: You must provide either 'instance' or 'module'.")
    if instance and module:
        raise ValueError("❌ Routing error: Provide ONLY ONE of 'instance' or 'module', not both.")

    if data is None: data = {}
    req_headers = headers or {}
    req_headers[API_KEY_HEADER_NAME] = SECRET_TOKEN

    port, contract_data = asyncio.run(fetch_global_contract())
    if not contract_data:
        raise RuntimeError("❌ MVP Framework mesh is empty or unreachable.")

    modules = contract_data.get("modules", [])

    match = _resolve_target(modules, instance=instance, module=module)

    if not match:
        target_desc = f"instance ID '{instance}'" if instance else f"module name '{module}'"
        raise ValueError(f"❌ No matching service found for {target_desc}")

    base_url = match.get("base_url")
    if not base_url:
        target_desc = instance or module
        raise RuntimeError(f"❌ No base_url found for resolved target '{target_desc}'")

    url = f"{base_url}/{endpoint.lstrip('/')}"

    try:
        resp = requests.post(url, json=data, headers=req_headers)

        if resp.headers.get("X-MVP-Mock-Fallback") == "true":
            err_msg = resp.headers.get("X-MVP-Original-Error", "Unknown")
            typer.secho(
                f"WARNING: Mock tier is responding on endpoint {url}. Reason: {err_msg}",
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


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return err.errno == errno.EPERM


def _resolve_instance_pid(instance: str) -> Optional[int]:
    """Определяет PID инстанса по точному файлу метаданных в RUN_DIR или лог-файлу (без ps aux)."""
    meta = read_instance_run_metadata(instance)
    if meta and isinstance(meta.get("pid"), int):
        return meta["pid"]

    if LOG_DIR.exists():
        pattern = re.compile(rf"^mvp-{re.escape(instance)}-pid(\d+)\.log$")
        for log_file in LOG_DIR.iterdir():
            match = pattern.match(log_file.name)
            if match:
                return int(match.group(1))
    return None


def _terminate_pid(pid: int) -> None:
    """Кроссплатформенно завершает процесс/группу процессов с эскалацией до SIGKILL по таймауту."""
    if not _is_pid_running(pid):
        return

    # На POSIX инстанс запускается с start_new_session=True, поэтому гасим всю группу (шлюз + воркеры)
    if hasattr(os, "killpg") and os.name != "nt":
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    deadline = time.time() + FRAMEWORK_PURGE_WAIT_TIMEOUT_SEC
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return
        time.sleep(FRAMEWORK_PURGE_POLL_INTERVAL_SEC)

    kill_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if hasattr(os, "killpg") and os.name != "nt":
        try:
            os.killpg(pid, kill_sig)
        except OSError:
            try:
                os.kill(pid, kill_sig)
            except OSError:
                pass
    else:
        try:
            os.kill(pid, kill_sig)
        except OSError:
            pass


def purge(instance: str):
    pid = _resolve_instance_pid(instance)
    if pid is None:
        raise ValueError(f"Instance '{instance}' not found in RUN_DIR or LOG_DIR")

    try:
        _terminate_pid(pid)
    finally:
        cleanup_instance_resources(instance)

def switch(instance: str, tier: str) -> Any:
    """
    Программный API для переключения активного тира компонента.
    tier должен быть одним из: "mock", "prod", "dev".
    """
    if tier not in {"mock", "prod", "dev"}:
        raise ValueError("Tier must be one of: mock, prod, dev")

    # instance - это точно instance_id
    return call(endpoint="_sys/switch", instance=instance, data={"tier": tier})

def syslog(*, module: Optional[str] = None, instance: Optional[str] = None, follow: bool = False) -> str:
    """Fetch logs explicitly by module name or instance ID."""
    if not follow:
        return call(endpoint="syslog", module=module, instance=instance)
    else:
        port, contract_data = asyncio.run(fetch_global_contract())
        if not contract_data:
            raise RuntimeError("❌ MVP Framework mesh is empty or unreachable.")

        modules = contract_data.get("modules", [])

        # Используем наш новый чистый хук Service Discovery
        match = _resolve_target(modules, instance=instance, module=module)

        if not match:
            target_desc = f"instance ID '{instance}'" if instance else f"module name '{module}'"
            raise ValueError(f"❌ No matching service found for {target_desc}")

        base_url = match.get("base_url")
        url = f"{base_url}/syslog-stream"

        try:
            with requests.post(url, stream=True, headers={API_KEY_HEADER_NAME: SECRET_TOKEN}) as resp:
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
    headers = {"X-Mesh-Version": MESH_PROTOCOL_VERSION, API_KEY_HEADER_NAME: SECRET_TOKEN}
    candidate_bases = list(SEED_PEERS) + [
        f"http://{DISCOVERY_HOST}:{port}"
        for port in range(PORT_RANGE[0], PORT_RANGE[1])
    ]
    async with httpx.AsyncClient(timeout=FRAMEWORK_PROBE_TIMEOUT_SEC) as client:
        for base in candidate_bases:
            try:
                probe = await client.post(
                    f"{base}/_gossip",
                    headers=headers,
                    json={"sender_id": "cli", "registry": {}}
                )
                if probe.status_code == 200:
                    openapi_resp = await client.get(
                        f"{base}/openapi",
                        headers=headers,
                        timeout=FRAMEWORK_OPENAPI_TIMEOUT_SEC
                    )
                    if openapi_resp.status_code == 200:
                        port_str = base.rsplit(":", 1)[-1]
                        resolved_port = int(port_str) if port_str.isdigit() else base
                        return resolved_port, openapi_resp.json()
            except Exception:
                continue
    return None, None

def resolve_schema_type(schema_ref: dict, components: dict) -> str:
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
            manifest_path = module.get("contract_path", "")
            active_tier = module.get("active_tier", "prod")

            header = Text()
            header.append("Component ", style="dim")
            header.append(f"{title}\n", style="bold cyan")

            if subtitle:
                header.append("Subtitle ", style="dim")
                header.append(f"{subtitle}\n", style="italic")

            header.append("Tier ", style="dim")
            tier_color = "magenta" if active_tier == "dev" else ("green" if active_tier == "prod" else "red")
            header.append(f"{active_tier.upper()}\n\n", style=f"bold {tier_color}")

            header.append("Instance ", style="dim")
            header.append(f"{instance_id}\n", style="yellow")

            if manifest_path:
                header.append("Contract ", style="dim")
                header.append(f"{manifest_path}\n", style="green")

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

def ls(component: Optional[str] = None):
    """Programmatic API to list all active modules dynamically from the Gossip network."""
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
    ls(component)


@app.command(name="call")
def cli_call(
    endpoint: str = typer.Argument(..., help="The endpoint path to call (e.g., read_excel)"),
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Target module name (e.g., 'Excel Reader')"),
    instance: Optional[str] = typer.Option(None, "--instance", "-i", help="Target instance ID (32-character hash)"),
    json_parts: Optional[List[str]] = typer.Argument(None, help="Optional JSON payload split into parts"),
    tier: Optional[str] = typer.Option(None, "--tier", help="Target tier to route to (prod, dev, mock)")
):
    """Call an endpoint on a deployed instance, optionally passing JSON input."""
    # Явная валидация флагов на уровне CLI для удобного UX
    if not instance and not module:
        typer.echo("❌ CLI Error: You must provide either --instance (-i) or --module (-m).")
        raise typer.Exit(1)
    if instance and module:
        typer.echo("❌ CLI Error: Provide ONLY ONE of --instance or --module, not both.")
        raise typer.Exit(1)

    json_data = {}
    if json_parts:
        json_str = " ".join(json_parts)
        try:
            json_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ Invalid JSON: {e}")
            raise typer.Exit(1)

    headers = {}
    if tier:
        headers["X-MVP-Tier"] = tier

    try:
        # Пробрасываем флаги в строго именованную функцию ядра
        result = call(
            endpoint=endpoint,
            module=module,
            instance=instance,
            data=json_data,
            headers=headers
        )

        if isinstance(result, (dict, list)):
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(result))

    except Exception as e:
        typer.echo(str(e))
        raise typer.Exit(1)


@app.command(name="contract")
def cli_contract(
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Target module name (e.g., 'Excel Reader')"),
    instance: Optional[str] = typer.Option(None, "--instance", "-i", help="Target instance ID (32-character hash)")
):
    """Fetch contract JSON from a deployed component instance."""
    if not instance and not module:
        typer.echo("❌ CLI Error: You must provide either --instance (-i) or --module (-m).")
        raise typer.Exit(1)
    if instance and module:
        typer.echo("❌ CLI Error: Provide ONLY ONE of --instance or --module, not both.")
        raise typer.Exit(1)

    try:
        res = contract(module=module, instance=instance)
        if isinstance(res, (dict, list)):
            typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            typer.echo(str(res))
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)

@app.command(name="switch")
def cli_switch(instance: str, tier: str = typer.Argument(..., help="prod | dev | mock")):
    """Switch default traffic routing for the component."""
    try:
        switch(instance, tier)
        typer.echo(f"🔄 Switched default tier for {instance} to: {tier.upper()}")
    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)

@app.command(name="update")
def cli_update(
    instance: str,
    dev: bool = typer.Option(False, "--dev", help="Update and restart dev tier from its branch"),
    prod: bool = typer.Option(False, "--prod", help="Update and restart prod tier based on contract")
):
    """Update source code and restart specific tiers based on YAML contract."""
    if not dev and not prod:
        typer.echo("❌ Please specify which tier to update: --dev or --prod (e.g., mvp update <id> --prod)")
        raise typer.Exit(1)

    try:
        if dev:
            call(endpoint="_sys/update_dev", instance=instance)
            typer.echo(f"✅ Dev tier of {instance} successfully updated and restarted.")
        if prod:
            call(endpoint="_sys/update_prod", instance=instance)
            typer.echo(f"✅ Prod tier of {instance} successfully updated based on contract and restarted.")
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

@app.command()
def log(id: str, follow: bool = typer.Option(False, "-f", "--follow", help="Follow the log output")):
    """Show log output of a specific component instance by ID."""
    log_dir = LOG_DIR
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
                        time.sleep(FRAMEWORK_LOG_FOLLOW_SLEEP_SEC)
            else:
                typer.echo(f.read())
    except Exception as e:
        typer.echo(f"❌ Failed to read log file: {e}")
        raise typer.Exit(1)

@app.command()
def ask(question: str):
    """Ask the AI assistant about available components or their functionality."""
    typer.echo(f"🤖 Asking AI: {question}")

@app.command(name="syslog")
def cli_syslog(
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Target module name (e.g., 'Excel Reader')"),
    instance: Optional[str] = typer.Option(None, "--instance", "-i", help="Target instance ID (32-character hash)"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs continuously")
):
    """Fetch logs from the component's syslog endpoint via API."""
    if not instance and not module:
        typer.echo("❌ CLI Error: You must provide either --instance (-i) or --module (-m).")
        raise typer.Exit(1)
    if instance and module:
        typer.echo("❌ CLI Error: Provide ONLY ONE of --instance or --module, not both.")
        raise typer.Exit(1)

    try:
        if follow:
            syslog(module=module, instance=instance, follow=True)
        else:
            logs = syslog(module=module, instance=instance, follow=False)
            print(logs, end="")
    except Exception as e:
        typer.secho(f"❌ Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

if __name__ == "__main__":
    app()
