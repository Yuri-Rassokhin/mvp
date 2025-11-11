import streamlit as st
import json
from pathlib import Path
import requests
import time

STATE_FILE = Path.home() / ".mvp" / "gui-state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)



def load_state():
    if not STATE_FILE.exists():
        return []

    try:
        with open(STATE_FILE, "r") as f:
            raw = f.read().strip()
            if not raw:
                return []
            state = json.loads(raw)
            if isinstance(state, list):
                return state
    except json.JSONDecodeError:
        pass
    except Exception:
        pass

    # Если что-то пошло не так — удаляем файл
    try:
        STATE_FILE.unlink()
    except Exception:
        pass

    return []



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

            payload = {}
            for k, v in values.items():
                t = inputs[k]
                if t == "str":
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



def render_gui():
    if not STATE_FILE.exists():
        st.info("No components attached yet.")
        return

    try:
        with open(STATE_FILE, "r") as f:
            raw = f.read().strip()
            if not raw:
                st.info("No components attached yet.")
                return
            blocks = json.loads(raw)
            if not isinstance(blocks, list) or len(blocks) == 0:
                st.info("No components attached yet.")
                return

        for comp in blocks:
            if not isinstance(comp, dict):
                continue
            render_component_block(comp)

    except json.JSONDecodeError:
        st.warning("⚠️ GUI state file is empty or invalid. Try re-attaching components.")
        return
    except Exception as e:
        st.error(f"Failed to load GUI state: {e}")



def main():
    st.set_page_config(page_title="MVP Desktop", layout="wide")
    st.markdown("<style>footer {visibility: hidden;}</style>", unsafe_allow_html=True)
    render_gui()
    time.sleep(1)
    st.rerun()
