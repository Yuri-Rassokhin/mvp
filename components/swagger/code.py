from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, json, httpx
from pathlib import Path
from ui_template import html_template

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

attached = {}

@app.post("/attach")
async def attach(req: Request):
    data = await req.json()
    iid = data.get("instance_id")
    name = data.get("name")
    port = data.get("port")
    if iid and name and port:
        attached[iid] = {"name": name, "port": port}
    return {"status": "ok"}

@app.get("/logs/{instance_id}")
async def logs(instance_id: str):
    comp = attached.get(instance_id)
    if not comp:
        return {"status": "error", "detail": "Not attached"}
    try:
        r = httpx.get(f"http://127.0.0.1:{comp['port']}/output/log", timeout=2.0)
        return {"status": "ok", "log": r.text}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/", response_class=HTMLResponse)
async def ui():
    try:
        out = subprocess.check_output(["mvp", "call", "mvp-manager", "ls", "{}"], text=True)
        comps = json.loads(out)["components"]
    except:
        comps = []

    html = html_template.replace("__COMPONENTS_JSON__", json.dumps(comps))
    return HTMLResponse(content=html)

