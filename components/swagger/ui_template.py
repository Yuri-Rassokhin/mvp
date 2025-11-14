from textwrap import dedent

# HTML template with visual depth, transparent headline, and consistent widths
html_template = dedent("""
<!DOCTYPE html>
<html>
<head>
  <title>MVP Desktop</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      font-family: sans-serif;
      background: linear-gradient(135deg, #f0f4ff, #d9e4f5);
      background-image: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.6) 0%, transparent 60%),
                        radial-gradient(circle at 70% 70%, rgba(255,255,255,0.4) 0%, transparent 70%);
      background-repeat: no-repeat;
      background-attachment: fixed;
      overflow: hidden;
    }
    #logs {
      margin-top: 20px;
    }
    pre {
        width: 100%;
        box-sizing: border-box;
        white-space: pre-wrap;
        word-wrap: break-word;
    }
    .component {
      position: absolute;
      background-color: rgba(165, 202, 255, 0.9);
      border: 3px solid #5468C9;
      padding: 10px;
      border-radius: 10px;
      width: 300px;
      max-height: 75vh;
      overflow-y: scroll;
      scrollbar-width: none; /* Firefox */
      -ms-overflow-style: none;  /* IE 10+ */
      resize: both;
      box-sizing: border-box;
      transition: box-shadow 0.2s ease, transform 0.1s ease;
      box-shadow: 4px 4px 10px rgba(0, 0, 0, 0.15);
    }
    .component::-webkit-scrollbar {
         display: none;  /* Chrome, Safari */
    }
    .component:hover {
      box-shadow: 0 0 12px rgba(0, 128, 255, 0.5);
      transform: scale(1.01);
    }
    .context-menu {
      position: absolute;
      display: none;
      background: #fff;
      border: 1px solid #ccc;
      z-index: 1000;
      box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
    }
    .context-menu button {
      display: block;
      width: 100%;
      padding: 5px 10px;
      border: none;
      background: none;
      text-align: left;
    }
    .context-menu button:hover {
      background-color: #eef;
    }
    input, button, select, textarea {
      font-family: inherit;
      font-size: 1em;
    }
    textarea {
      width: 100%;
      height: 150px;
      resize: none;
    }
    h1.depth-title {
      position: absolute;
      top: 40%;
      left: 50%;
      transform: translate(-50%, -50%);
      font-size: 4em;
      color: rgba(0, 0, 64, 0.1);
      text-shadow: 0 10px 15px rgba(0,0,0,0.2);
      pointer-events: none;
      user-select: none;
      z-index: 0;
    }
  </style>
</head>
<body>
<h1 class="depth-title">MVP Desktop</h1>
<div id="logs"></div>
<div id="menu" class="context-menu"></div>
<script>
  const components = __COMPONENTS_JSON__;
  const attached = {};
  let contextTargetId = null;

  function attachComponent(comp) {
    if (attached[comp.id]) return;

    const div = document.createElement("div");
    div.className = "component";
    div.style.top = Math.random() * 400 + "px";
    div.style.left = Math.random() * 600 + "px";
    div.setAttribute("data-id", comp.id);

    const header = document.createElement("h4");
    header.textContent = comp.name + " (" + comp.id.slice(0, 6) + ")";
    div.appendChild(header);

    const pre = document.createElement("pre");
    pre.id = "log-" + comp.id;
    pre.textContent = "Right-click to select endpoint";
    div.appendChild(pre);

    makeDraggable(div);
    document.getElementById("logs").appendChild(div);
    attached[comp.id] = {div, comp};
  }

  function makeDraggable(el) {
    el.onmousedown = function (e) {
      if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
      let offsetX = e.clientX - el.offsetLeft;
      let offsetY = e.clientY - el.offsetTop;

      function move(e) {
        el.style.left = (e.clientX - offsetX) + 'px';
        el.style.top = (e.clientY - offsetY) + 'px';
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', () => {
        document.removeEventListener('mousemove', move);
      }, { once: true });
    }
  }

  function showContextMenu(e, id) {
    e.preventDefault();
    const comp = attached[id].comp;
    const menu = document.getElementById("menu");
    menu.innerHTML = "";
    comp.endpoints.forEach(ep => {
      const btn = document.createElement("button");
      btn.textContent = ep;
      btn.onclick = () => {
        renderEndpointForm(id, ep);
        menu.style.display = "none";
      };
      menu.appendChild(btn);
    });
    menu.style.left = e.pageX + "px";
    menu.style.top = e.pageY + "px";
    menu.style.display = "block";
  }

  function renderEndpointForm(id, endpoint) {
    const comp = attached[id].comp;
    const div = attached[id].div;
    div.innerHTML = `<h4>${comp.name} (${id.slice(0,6)}) — ${endpoint}</h4>`;

    const form = document.createElement("form");
    const inputs = comp.io?.[endpoint]?.inputs || {};
    const inputValues = {};

    Object.entries(inputs).forEach(([name, type]) => {
      const label = document.createElement("label");
      label.textContent = name + " (" + type + "):";
      const input = document.createElement("textarea");
      input.name = name;
      input.placeholder = `Enter ${type}...`;
      form.appendChild(label);
      form.appendChild(document.createElement("br"));
      form.appendChild(input);
      form.appendChild(document.createElement("br"));
    });

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = "Call";
    form.appendChild(submit);

    const output = document.createElement("pre");
    output.id = "result-" + id;
    output.textContent = "Result will appear here";
    output.style.width = "100%";
    output.style.boxSizing = "border-box";

    form.onsubmit = (e) => {
      e.preventDefault();
      const payload = {};
      const data = new FormData(form);

      for (const [key, val] of data.entries()) {
        try {
          payload[key] = JSON.parse(val);
        } catch {
          payload[key] = val;
        }
      }

      const jsonPayload = Object.keys(payload).length > 0 ? payload : {};

      fetch(`/proxy/${comp.port}/${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(jsonPayload)
      })
        .then(r => r.json())
        .then(data => output.textContent = JSON.stringify(data, null, 2))
        .catch(err => output.textContent = "Error: " + err);
    };

    form.appendChild(document.createElement("br"));
    form.appendChild(output);
    div.appendChild(form);
  }

  document.addEventListener("contextmenu", e => {
    const box = e.target.closest(".component");
    if (box) {
      const id = box.getAttribute("data-id");
      showContextMenu(e, id);
    } else {
      document.getElementById("menu").style.display = "none";
    }
  });

  components.forEach(attachComponent);
</script>
</body>
</html>
""")

