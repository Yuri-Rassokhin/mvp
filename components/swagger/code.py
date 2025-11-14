from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, json, httpx
from ui_template import html_template
import requests

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

attached = {}

@app.post("/attach")
async def attach(req: Request):
    data = await req.json()
    iid = data.get("instance_id")
    name = data.get("name")
    port = data.get("port")
    io = data.get("io")
    if iid and name and port:
        attached[iid] = {"name": name, "port": port, "io": io}
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

@app.post("/call/{instance_id}/{endpoint}")
async def call(instance_id: str, endpoint: str, req: Request):
    comp = attached.get(instance_id)
    if not comp:
        return {"status": "error", "detail": "Not attached"}
    try:
        data = await req.json()
        r = httpx.post(f"http://127.0.0.1:{comp['port']}/{endpoint}", json=data, timeout=4.0)
        return {"status": "ok", "result": r.json()}
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

@app.post("/proxy/{port}/{endpoint}")
async def proxy(port: int, endpoint: str, request: Request):
    body = await request.json()
    url = f"http://127.0.0.1:{port}/{endpoint}"
    try:
        resp = requests.post(url, json=body)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

