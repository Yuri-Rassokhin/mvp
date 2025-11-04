import os
import requests
import json
import typer
from pathlib import Path
from dotenv import load_dotenv


def get_url() -> str:
    """
    Читает URL базы данных из файла ~/.mvp/database.
    """
    db_path = Path.home() / ".mvp" / "database"
    if not db_path.exists():
        typer.echo("❌ Database URL file ~/.mvp/database not found")
        raise typer.Exit(1)

    url = db_path.read_text().strip()
    if not url:
        typer.echo("❌ Database URL file ~/.mvp/database is empty")
        raise typer.Exit(1)

    return url



def remove_instance(instance: str):
    try:
        ords_url = get_url()
        url = f"{ords_url}/instance/{instance}"
        resp = requests.delete(url)
        if resp.status_code != 200:
            typer.echo(f"⚠️ Failed to delete instance from DB: {resp.status_code} {resp.text}")
    except Exception as e:
        typer.echo(f"⚠️  Exception during instance removal: {e}")



def add_instance(component: str, instance: str, description: str, url: str, endpoints: list[str]):
    """
    Записывает инстанс компонента в таблицу MVP_DEVELOPMENT через ORDS.
    URL берётся из переменной окружения ORDS_MVP_URL.
    """
    try:
        ords_url = get_url()
    except Exception as e:
        typer.echo(f"⚠️  Exception while adding instance: {e}")
        return

    payload = {
        "component": component,
        "instance": instance,
        "description": description,
        "url": url,
        "endpoints": ", ".join(endpoints)
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(ords_url, json=payload, headers=headers)
        if response.status_code != 201:
            print(f"ORDS returned {response.status_code}: {response.text}")
    except Exception as e:
        print(f"❌ Failed to add instance to database: {e}")

