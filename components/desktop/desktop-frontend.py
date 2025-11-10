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

import streamlit as st
import json
from pathlib import Path
import requests
import time

# Функция: рендерит блок для компоненты
def render_component_block(comp):
    st.markdown("---")
    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown(f"### 🔧 {comp['name']} ({comp['id'][:6]})")
    with col2:
        if st.button(f"❌ Detach", key=f"detach-{comp['id']}"):
            requests.post("http://localhost:8501/detach", json={"instance_id": comp['id']})
            st.rerun()

    endpoints = comp.get("endpoints", [])
    io = comp.get("io", {})

    if not endpoints:
        st.info("No endpoints defined.")
        return

    selected_ep = st.selectbox("Select endpoint", endpoints, key=f"ep-{comp['id']}")
    sig = io.get(selected_ep, {})
    inputs = sig.get("inputs", {})

    st.markdown("#### 🔤 Input parameters")
    values = {}
    for name, typ in inputs.items():
        if typ in ["str", "int", "float"]:
            values[name] = st.text_input(f"{name} ({typ})", key=f"{comp['id']}-{selected_ep}-{name}")
        else:
            values[name] = st.text_area(f"{name} ({typ}) — JSON", key=f"{comp['id']}-{selected_ep}-{name}-json")

    if st.button("▶️ Run", key=f"run-{comp['id']}-{selected_ep}"):
        try:
            ip = comp["ip"]
            port = comp["port"]
            url = f"http://{ip}:{port}/{selected_ep}"

            # Преобразование значений
            payload = {}
            for k, v in values.items():
                t = inputs[k]
                if t in ["str"]:
                    payload[k] = v
                elif t == "int":
                    payload[k] = int(v)
                elif t == "float":
                    payload[k] = float(v)
                else:
                    payload[k] = json.loads(v)

            r = requests.post(url, json=payload)
            r.raise_for_status()
            st.success("✅ Success")
            st.json(r.json())
        except Exception as e:
            st.error(f"❌ Error: {e}")

# Основной рендеринг
def render_gui():
    if not STATE_FILE.exists():
        st.info("No components attached yet.")
        return

    try:
        with open(STATE_FILE, "r") as f:
            blocks = json.load(f)
        for comp in blocks:
            render_component_block(comp)
    except Exception as e:
        st.error(f"Failed to load GUI state: {e}")

# Постоянный auto-refresh
def main():
    st.markdown("<style>footer {visibility: hidden;}</style>", unsafe_allow_html=True)
    render_gui()
    time.sleep(1)
    st.rerun()

main()

