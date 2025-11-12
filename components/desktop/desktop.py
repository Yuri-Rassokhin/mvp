from pathlib import Path
from typing import Dict
import os
import json
import sys
import signal
import atexit
import subprocess
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))
from mesh import get_component_status
from desktop_pipe import (send_to_desktop, start_streamlit)



def desktop():
    html = """<meta http-equiv="refresh" content="0; url=http://localhost:8999">"""
    return HTMLResponse(content=html)



def attach(instance_id: str):
    comp = get_component_status(instance_id)
    if not comp:
        print(f"⚠️ No component found with ID: {instance_id}")
        return
    send_to_desktop(comp)



def detach(instance_id: str):
    pass



def launch():
    start_streamlit()

