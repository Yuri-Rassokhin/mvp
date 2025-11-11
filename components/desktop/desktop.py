import subprocess
from pathlib import Path
from fastapi.responses import HTMLResponse
from fastapi import FastAPI
import atexit
import signal

def desktop():
    html = """<meta http-equiv="refresh" content="0; url=http://localhost:8999">"""
    return HTMLResponse(content=html)

app: FastAPI = None  # MVP сам подставит объект сюда

streamlit_proc = None

def launch():
    log_file = Path.home() / ".mvp" / "logs" / "gui-desktop-frontend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "w") as out:
        global streamlit_proc
        streamlit_proc = subprocess.Popen(
            [
                "streamlit", "run", "desktop-frontend.py",
                "--server.port", "8999",
                "--server.headless", "true"
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent  # чтобы запуск шел из компонента
        )

    # Регистрация завершения при остановке FastAPI
    if app:
        @app.on_event("shutdown")
        def on_shutdown():
            cleanup()

    # На всякий случай
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda sig, frame: cleanup())



def cleanup():
    global streamlit_proc
    if streamlit_proc and streamlit_proc.poll() is None:
        print("Cleaning up Streamlit process...")
        streamlit_proc.terminate()
        try:
            streamlit_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streamlit_proc.kill()



import json
from pathlib import Path
from typing import Dict

import sys
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))
from mesh import get_component_status



desktop_state = []



def attach(instance_id: str):
    global desktop_state

    try:
        component = get_component_status(instance_id)
        if not component:
            print(f"attaching to desktop: instance {instance_id} not found")
            return

        # Если уже подключён — не дублируем
        if any(entry.get("id") == instance_id for entry in desktop_state):
            print(f"attaching to desktop: instance {instance_id} already attached")
            return

        # Добавляем компонент как блок
        desktop_state.append(component)
        print(f"instance {instance_id} attached")

    except Exception as e:
        print(f"error attaching instance {instance_id}: {e}")



# Заглушка для detach — позже можно передавать в GUI
def detach(instance_id: str):
    pass

