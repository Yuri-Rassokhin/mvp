# gui.py — Streamlit GUI фронтенд

import streamlit as st
import streamlit.components.v1 as components
import json
from pathlib import Path

STATE_FILE = Path.home() / ".mvp" / "gui-state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Загрузка attach-блоков
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []

blocks = load_state()

# Streamlit UI
st.set_page_config(page_title="MVP Desktop", layout="wide")

html = """
<style>
body {
    background-color: white;
}
</style>

<div id='desktop' style='
    position:relative;
    width:100%;
    height:90vh;
    background:white;
    padding:0;
    margin:0;
    overflow:hidden;
'>

<div id="desktop-title" style="
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 5em;
    font-weight: bold;
    font-family: sans-serif;
    color: #000000;
    opacity: 0.05;
    z-index: 0;
    pointer-events: none;
">
    MVP Desktop
</div>

"""

for i, block in enumerate(blocks):
    html += f"""
        <div id='block-{i}' style="
        position:absolute;
        top:{50+i*70}px;
        left:{50+i*100}px;
        width:300px;
        padding:10px;
        background:#f0f8ff;
        border:1px solid #ccc;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.1);
        border-radius: 8px;
        cursor:move;
        font-family: sans-serif;
        z-index:10;"
        onmousedown="dragElement(this)"
    >
        <b>{block}</b><br>
        <div style='font-size:smaller;color:#444;'>[log output will go here]</div>
    </div>
    """

html += """
</div>

<script>
function dragElement(elmnt) {
  let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
  elmnt.onmousedown = dragMouseDown;

  function dragMouseDown(e) {
    e.preventDefault();
    pos3 = e.clientX;
    pos4 = e.clientY;
    document.onmouseup = closeDragElement;
    document.onmousemove = elementDrag;
  }

  function elementDrag(e) {
    e.preventDefault();
    pos1 = pos3 - e.clientX;
    pos2 = pos4 - e.clientY;
    pos3 = e.clientX;
    pos4 = e.clientY;
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

