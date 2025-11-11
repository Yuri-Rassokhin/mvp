import streamlit as st
import json
from pathlib import Path
import requests
import time



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



def render_gui(components):
    if not components:
        st.info("No components attached yet.")
        return

    for comp in components:
        render_component_block(comp)



