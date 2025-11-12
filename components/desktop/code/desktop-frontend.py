import threading
import json
from pathlib import Path
import streamlit as st
from desktop_ui import render_desktop_background
from desktop_logic import render_gui
import time

PIPE_PATH = Path("/tmp/mvp_desktop_pipe")

incoming = []

def listen_pipe():
    global incoming

    print("🔁 Pipe listener started")
    while True:
        try:
            with open(PIPE_PATH, 'r') as pipe:
                while True:
                    line = pipe.readline()
                    if not line:
                        continue
                    try:
                        comp = json.loads(line)
                        
                        if isinstance(comp, list):
                            comp = comp[0]

                        with open("/tmp/mvp_desktop_debug.log", "a") as f:
                            f.write(f"📥 Received via pipe: {comp}\n")
                        # Добавляем, если компонента с таким id еще нет
#                        if not any(c["id"] == comp["id"] for c in st.session_state.components):
#                        st.session_state.components.append(comp)
#                        st.session_state.redraw = True
                        incoming.append(comp)   
                        with open("/tmp/mvp_desktop_debug.log2", "a") as f:
                            f.write(f"📥 Received via pipe: {incoming}\n")
                    except Exception as e:
                        with open("/tmp/exception.log") as f:
                            f.write(f"❌ Invalid pipe data: {e}")
        except Exception as outer:
            print(f"❌ Failed to open pipe: {outer}")

if "redraw" not in st.session_state:
    st.session_state.redraw = False

if "components" not in st.session_state:
    st.session_state.components = []

# 🧠 Инициализация session_state
if "pipe_started" not in st.session_state:
    threading.Thread(target=listen_pipe, daemon=True).start()
    st.session_state.pipe_started = True

# 🎨 UI
st.set_page_config(page_title="MVP Desktop", layout="wide")
st.markdown("<style>footer {visibility: hidden;}</style>", unsafe_allow_html=True)

render_desktop_background([c["id"] for c in st.session_state.components])
render_gui(st.session_state.components)

while True:
    time.sleep(0.5)
    if incoming:
        st.warning("HERE!")
        render_gui(incoming)
        incoming = []
#        st.rerun()

