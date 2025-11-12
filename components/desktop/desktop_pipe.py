import subprocess
import signal
import atexit
import time
import os
import json
from pathlib import Path



PIPE_PATH = Path("/tmp/mvp_desktop_pipe")



def send_to_desktop(component: dict):
    f = open("/tmp/debug.log", "a")
    f.write("Sending...")

    try:
        with open(PIPE_PATH, 'w') as pipe:
            json.dump(component, pipe)
            pipe.write('\n')  # важно — чтобы можно было читать построчно
        f.write(f"✅ Component {component} sent via pipe.")
    except Exception as e:
        f.write(f"❌ Error writing to pipe: {e}")

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

def start_streamlit():
    global streamlit_proc

    ensure_pipe()

    log_file = Path.home() / ".mvp" / "logs" / "gui-desktop-frontend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "w") as out:
        streamlit_proc = subprocess.Popen(
            [
                "streamlit", "run", "desktop-frontend.py",
                "--server.port", "8999",
                "--server.headless", "true"
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent  # или укажи нужную папку
        )

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda sig, frame: cleanup())


