import subprocess
import signal
import atexit
import time
import os
import json
from pathlib import Path



ux_proc = None

def desktop():
    return {"status": "ok"}



def cleanup():
    global ux_proc

    if ux_proc and ux_proc.poll() is None:
        ux_proc.terminate()
        try:
            ux_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ux_proc.kill()



def start_ux():
    global ux_proc

    with open("/tmp/log", "a") as f:
        f.write(f"{Path(__file__).resolve().parent}")

    log_file = Path.home() / ".mvp" / "logs" / "gui-desktop-frontend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as out:
        ux_proc = subprocess.Popen(
            [ "uvicorn", "code:app", "--host", "0.0.0.0", "--port", "8999" ],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent
        )
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda sig, frame: cleanup())
