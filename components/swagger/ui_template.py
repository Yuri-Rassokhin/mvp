html_template = """<html>
<head>
  <title>MVP Log Viewer</title>
  <style>
    body { font-family: sans-serif; }
    .component-box {
        position: absolute;
        top: 100px;
        left: 100px;
        width: 400px;
        max-height: 75vh;
        padding: 10px;
        background: #eef;
        border: 1px solid #88f;
        border-radius: 10px;
        resize: both;
        overflow: auto;
        box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
    }
    #contextMenu {
        position: absolute;
        display: none;
        z-index: 1000;
        width: 220px;
        background: white;
        border: 1px solid #ccc;
        border-radius: 6px;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.15);
    }
    #contextMenu div {
        padding: 8px 12px;
        cursor: pointer;
    }
    #contextMenu div:hover {
        background: #eef;
    }
  </style>
</head>
<body>
  <h2>MVP Component Logs</h2>
  <div id="logs"></div>
  <div id="contextMenu"></div>

  <script>
    let components = __COMPONENTS_JSON__;
    let attached = {};

    document.addEventListener("contextmenu", function(event) {
        event.preventDefault();
        const menu = document.getElementById("contextMenu");
        menu.innerHTML = "";
        components.forEach(comp => {
            const item = document.createElement("div");
            item.textContent = comp.name + " (" + comp.id.slice(0,6) + ")";
            item.onclick = function() { attach(comp); menu.style.display = "none"; };
            menu.appendChild(item);
        });
        menu.style.left = event.pageX + "px";
        menu.style.top = event.pageY + "px";
        menu.style.display = "block";
    });

    document.addEventListener("click", function() {
        document.getElementById("contextMenu").style.display = "none";
    });

    function attach(comp) {
        if (attached[comp.id]) return;
        fetch("/attach", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                instance_id: comp.id,
                name: comp.name,
                port: comp.port
            })
        }).then(() => {
            attached[comp.id] = true;
            const div = document.createElement("div");
            div.className = "component-box";
            div.innerHTML = "<h4>" + comp.name + "</h4><pre id='log-" + comp.id + "'>loading...</pre>";
            document.getElementById("logs").appendChild(div);
            makeDraggable(div);
        });
    }

    function refresh() {
        for (const id in attached) {
            fetch("/logs/" + id)
                .then(r => r.json())
                .then(d => {
                    if (d.status === "ok")
                        document.getElementById("log-" + id).textContent = d.log;
                });
        }
    }
    setInterval(refresh, 1000);

    function makeDraggable(el) {
        let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
        el.onmousedown = dragMouseDown;
        function dragMouseDown(e) {
            e.preventDefault();
            pos3 = e.clientX;
            pos4 = e.clientY;
            document.onmouseup = closeDragElement;
            document.onmousemove = elementDrag;
        }
        function elementDrag(e) {
            pos1 = pos3 - e.clientX;
            pos2 = pos4 - e.clientY;
            pos3 = e.clientX;
            pos4 = e.clientY;
            el.style.top = (el.offsetTop - pos2) + "px";
            el.style.left = (el.offsetLeft - pos1) + "px";
        }
        function closeDragElement() {
            document.onmouseup = null;
            document.onmousemove = null;
        }
    }
  </script>
</body>
</html>"""

