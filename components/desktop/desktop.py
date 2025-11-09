import subprocess
from pathlib import Path
from fastapi.responses import HTMLResponse

def desktop():
    html = """<meta http-equiv="refresh" content="0; url=http://localhost:8999">"""
    return HTMLResponse(content=html)

def launch():
    log_file = Path.home() / ".mvp" / "logs" / "gui-desktop-frontend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "w") as out:
        subprocess.Popen(
            [
                "streamlit", "run", "desktop-frontend.py",
                "--server.port", "8999",
                "--server.headless", "true"
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent  # чтобы запуск шел из компонента
        )

# Заглушка для attach — позже можно передавать в GUI
def attach(instance_id: str):
    pass

# Заглушка для detach — позже можно передавать в GUI
def detach(instance_id: str):
    pass

