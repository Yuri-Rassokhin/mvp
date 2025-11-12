from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, json, httpx
from pathlib import Path

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

    opts = "\n".join([
        f"<option value='{c['id']}' data-name='{c['name']}' data-port='{c['port']}'>{c['name']} ({c['id'][:6]})</option>"
        for c in comps
    ])

    template_path = Path(__file__).parent / "viewer_template.html"
    template = template_path.read_text()
    html = template.replace("{options}", opts)
    return HTMLResponse(content=html)

