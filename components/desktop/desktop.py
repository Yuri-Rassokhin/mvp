from pathlib import Path
from typing import Dict
import os
import json
import time
import sys
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))
from mesh import get_component_status
import subprocess
import signal
import atexit



PIPE_PATH = Path("/tmp/mvp_desktop_pipe")



def desktop():
    html = """<meta http-equiv="refresh" content="0; url=http://localhost:8999">"""
    return HTMLResponse(content=html)



def ensure_pipe():
    if PIPE_PATH.exists():
        PIPE_PATH.unlink()  # удалить старый
    os.mkfifo(PIPE_PATH)



def send_to_desktop(component: dict):
    try:
        with open(PIPE_PATH, 'w') as pipe:
            json.dump(component, pipe)
            pipe.write('\n')  # важно — чтобы можно было читать построчно
        print(f"✅ Component {component} sent via pipe.")
    except Exception as e:
        print(f"❌ Error writing to pipe: {e}")



def attach(instance_id: str):
    comp = get_component_status(instance_id)
    if comp:
        send_to_desktop(comp)



def detach(instance_id: str):
    pass



streamlit_proc = None



def ensure_pipe():
    if PIPE_PATH.exists():
        PIPE_PATH.unlink()  # Удалим старый pipe, если остался после сбоя
    os.mkfifo(PIPE_PATH)
    print(f"✅ Named pipe created at {PIPE_PATH}")



def cleanup():
    global streamlit_proc
    if streamlit_proc and streamlit_proc.poll() is None:
        print("🧹 Cleaning up Streamlit process...")
        streamlit_proc.terminate()
        try:
            streamlit_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streamlit_proc.kill()
    if PIPE_PATH.exists():
        PIPE_PATH.unlink()
        print("🧹 Named pipe removed.")



def launch():
    global streamlit_proc

    log_file = Path.home() / ".mvp" / "logs" / "gui-desktop-frontend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    ensure_pipe()

    with open(log_file, "w") as out:
        streamlit_proc = subprocess.Popen(
            [
                "streamlit", "run", "desktop-frontend.py",
                "--server.port", "8999",
                "--server.headless", "true"
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent  # Стартовать из каталога компонента
        )

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda sig, frame: cleanup())


