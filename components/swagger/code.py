from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, json, httpx

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

    html = f"""
    <html><head><title>MVP Log Viewer</title>
    <script>
    let attached = {{}};
    function attach() {{
        const sel = document.getElementById("sel");
        const id = sel.value;
        const name = sel.options[sel.selectedIndex].dataset.name;
        const port = sel.options[sel.selectedIndex].dataset.port;
        if (attached[id]) return;
        fetch("/attach", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{instance_id: id, name: name, port: port}})
        }}).then(() => {{
            attached[id] = true;
            const div = document.createElement("div");
            div.innerHTML = "<h4>" + name + "</h4><pre id='log-" + id + "'>loading...</pre>";
            document.getElementById("logs").appendChild(div);
        }});
    }}
    function refresh() {{
        for (const id in attached) {{
            fetch("/logs/" + id)
                .then(r => r.json())
                .then(d => {{
                    if (d.status === "ok")
                        document.getElementById("log-" + id).textContent = d.log;
                }});
        }}
    }}
    setInterval(refresh, 1000);
    </script></head>
    <body>
    <h2>MVP Component Logs</h2>
    <select id="sel">{opts}</select>
    <button onclick="attach()">Attach</button>
    <div id="logs"></div>
    </body></html>
    """
    return HTMLResponse(content=html)

