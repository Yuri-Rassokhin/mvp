from typing import Dict
import streamlit as st
import time
import threading
from pathlib import Path

# Глобальное состояние: какие инстансы подключены
attached_instances = set()

# Поток, который читает логи
def tail_logs():
    log_path = Path.home() / ".mvp" / "log"
    last_positions = {}

    while True:
        for instance_id in attached_instances:
            file_path = log_path / f"{instance_id}.log"
            if not file_path.exists():
                continue

            if instance_id not in last_positions:
                last_positions[instance_id] = 0

            with open(file_path, "r") as f:
                f.seek(last_positions[instance_id])
                lines = f.readlines()
                last_positions[instance_id] = f.tell()

            if lines:
                for line in lines:
                    st.session_state.logs.setdefault(instance_id, []).append(line.strip())

        time.sleep(1)

# Запуск логгера в фоне
def start_log_thread():
    if "log_thread_started" not in st.session_state:
        threading.Thread(target=tail_logs, daemon=True).start()
        st.session_state.log_thread_started = True
        st.session_state.logs = {}

# Этот endpoint покажет веб‑GUI
def gui():
    import streamlit as st

    st.set_page_config(page_title="MVP Log Viewer")
    st.title("MVP Log Viewer")

    start_log_thread()

    instance_id = st.text_input("Attach instance ID:")
    if st.button("Attach"):
        if instance_id:
            attached_instances.add(instance_id)

    for instance in list(attached_instances):
        st.subheader(f"Logs for: {instance}")
        logs = st.session_state.logs.get(instance, [])
        for line in logs[-50:]:  # Показываем последние 50 строк
            st.text(line)
        if st.button(f"Detach {instance}"):
            attached_instances.remove(instance)
            st.session_state.logs.pop(instance, None)

# Эти endpoints будут вызываться как mvp call gui attach <id> и detach <id>
def attach(instance_id: str):
    attached_instances.add(instance_id)
    return {"status": "attached", "instance": instance_id}

def detach(instance_id: str):
    attached_instances.discard(instance_id)
    return {"status": "detached", "instance": instance_id}

