# component.py — MVP obёrtka

import subprocess
import threading
import time
from fastapi import FastAPI
from pydantic import BaseModel
import requests

INSTANCE_ID = "gui-desktop"  # budet perekryt platformoy, esli nuzhno
STREAMLIT_PORT = 8999

app = FastAPI()

# Start Streamlit GUI v otdelnom potoke
def run_gui():
    subprocess.Popen([
        "streamlit", "run", "gui.py",
        "--server.port", str(STREAMLIT_PORT),
        "--server.headless", "true"
    ])

threading.Thread(target=run_gui, daemon=True).start()

# Delay na start GUI
threading.Thread(target=lambda: time.sleep(2), daemon=True).start()

# Tipy dannykh dlya attach/detach
class AttachRequest(BaseModel):
    instance_id: str

@app.post("/attach")
def attach(req: AttachRequest):
    try:
        res = requests.post(f"http://localhost:{STREAMLIT_PORT}/attach", json={"instance_id": req.instance_id})
        return res.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.post("/detach")
def detach(req: AttachRequest):
    try:
        res = requests.post(f"http://localhost:{STREAMLIT_PORT}/detach", json={"instance_id": req.instance_id})
        return res.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/manifest")
def manifest():
    return {
        "name": "gui-desktop",
        "instance": INSTANCE_ID,
        "type": "component",
        "description": "Draggable desktop GUI to visualize MVP components",
        "endpoints": {
            "/attach": {"method": "POST", "description": "Attach a component block"},
            "/detach": {"method": "POST", "description": "Detach a component block"},
            "/manifest": {"method": "GET"}
        }
    }

# gui.py — Streamlit desktop s drag-and-drop

import streamlit as st
import streamlit.components.v1 as components
import json
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from threading import Thread
import uvicorn

# Global state (in-memory)
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# FastAPI API-server dlya attach/detach iz obёrtki
api = FastAPI()
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@api.post("/attach")
async def api_attach(req: Request):
    data = await req.json()
    iid = data.get("instance_id")
    if iid and iid not in st.session_state.blocks:
        st.session_state.blocks.append(iid)
    return {"status": "attached", "instance_id": iid}

@api.post("/detach")
async def api_detach(req: Request):
    data = await req.json()
    iid = data.get("instance_id")
    if iid in st.session_state.blocks:
        st.session_state.blocks.remove(iid)
    return {"status": "detached", "instance_id": iid}

# Launch FastAPI server in background
Thread(target=lambda: uvicorn.run(api, host="0.0.0.0", port=8551, log_level="error"), daemon=True).start()

# Streamlit frontend
st.set_page_config(page_title="MVP Desktop", layout="wide")
st.title("🧠 MVP CogniVerse Desktop")

new_instance = st.text_input("Attach component instance ID")
if st.button("Attach"):
    if new_instance and new_instance not in st.session_state.blocks:
        st.session_state.blocks.append(new_instance)

html = """
<div id='desktop' style='position:relative;width:100%;height:800px;border:1px solid #ccc;background:#f9f9f9;'>
"""
for i, block in enumerate(st.session_state.blocks):
    html += f"""
    <div id='block-{i}' style='position:absolute;top:{50+i*70}px;left:{50+i*100}px;width:300px;padding:10px;background:#eef;border:1px solid #88f;cursor:move;z-index:10;' draggable='true' onmousedown='dragElement(this)'>
        <b>{block}</b><br>
        <div style='font-size:smaller;color:#444;'>[log output will appear here]</div>
    </div>
    """
html += """
</div>
<script>
function dragElement(elmnt) {
  let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
  elmnt.onmousedown = dragMouseDown;
  function dragMouseDown(e) {
    e = e || window.event; e.preventDefault();
    pos3 = e.clientX; pos4 = e.clientY;
    document.onmouseup = closeDragElement;
    document.onmousemove = elementDrag;
  }
  function elementDrag(e) {
    e = e || window.event; e.preventDefault();
    pos1 = pos3 - e.clientX; pos2 = pos4 - e.clientY;
    pos3 = e.clientX; pos4 = e.clientY;
    elmnt.style.top = (elmnt.offsetTop - pos2) + "px";
    elmnt.style.left = (elmnt.offsetLeft - pos1) + "px";
  }
  function closeDragElement() {
    document.onmouseup = null;
    document.onmousemove = null;
  }
}
</script>
"""
components.html(html, height=850, scrolling=True)


