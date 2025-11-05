import subprocess
import os
import json
from typing import Dict, List
from pathlib import Path

def add(path: str) -> Dict:
    try:
        result = subprocess.run(
            ["mvp", "add", path],
            capture_output=True,
            text=True,
            timeout=60
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

def rm(instance_id: str) -> Dict:
    try:
        result = subprocess.run(
            ["mvp", "rm", instance_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

def ls() -> Dict:
    status_path = Path.home() / ".mvp" / "status"
    if not status_path.exists():
        return {"status": "ok", "components": []}
    
    try:
        with open(status_path, "r") as f:
            components = json.load(f)
        return {
            "status": "ok",
            "count": len(components),
            "components": components
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to read status file: {e}"
        }

