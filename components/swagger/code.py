from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import asyncio
import httpx

app = FastAPI(
    title="MVP Swagger Desktop",
    description="Attach components and view their logs in real-time.",
    version="0.1.0"
)

# Allow local CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Memory store of attached instances
attached_instances = set()

class AttachRequest(BaseModel):
    instance_id: str

@app.post("/attach")
async def attach_component(req: AttachRequest):
    attached_instances.add(req.instance_id)
    return {"status": "attached", "instance_id": req.instance_id}

@app.get("/", response_class=HTMLResponse)
async def gui():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MVP Swagger Desktop</title>
        <style>
            body { font-family: sans-serif; margin: 20px; }
            .block { border: 1px solid #aaa; padding: 10px; margin: 10px 0; border-radius: 6px; background: #f5f5f5; }
            .log { background: black; color: lime; font-family: monospace; white-space: pre-wrap; padding: 5px; height: 150px; overflow-y: scroll; }
        </style>
    </head>
    <body>
        <h1>MVP Swagger Desktop</h1>
        <input id="instance" placeholder="Enter instance_id"/>
        <button onclick="attach()">Attach</button>
        <div id="components"></div>

        <script>
        async function attach() {
            const iid = document.getElementById("instance").value;
            if (!iid) return;
            await fetch("/attach", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ instance_id: iid })
            });
            const div = document.createElement("div");
            div.className = "block";
            div.id = `block-${iid}`;
            div.innerHTML = `<h3>${iid}</h3><div class='log' id='log-${iid}'>Loading...</div>`;
            document.getElementById("components").appendChild(div);
        }

        async function fetchLogs() {
            const blocks = document.querySelectorAll(".log");
            for (const block of blocks) {
                const iid = block.id.replace("log-", "");
                try {
                    const r = await fetch(`/logs/${iid}`);
                    const data = await r.json();
                    block.textContent = data.log;
                } catch (e) {
                    block.textContent = `[error loading log]`;
                }
            }
        }
        setInterval(fetchLogs, 1000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/logs/{instance_id}")
async def get_logs(instance_id: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://localhost:8502/output/log", timeout=2.0)
            r.raise_for_status()
            return {"log": r.text.strip()}
    except Exception as e:
        return JSONResponse(content={"log": f"[error: {e}]"}, status_code=500)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)

