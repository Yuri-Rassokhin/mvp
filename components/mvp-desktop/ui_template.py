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
      //transform: scale(1.01);
    }
    .context-menu {
      position: absolute;
      display: none;
      background: #fff;
      border: 1px solid #ccc;
      z-index: 9999;
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
    h1.depth-title {
      position: absolute;
      top: 40%;
      left: 50%;
      transform: translate(-50%, -50%);
      font-size: 10em;
      color: rgba(0, 0, 64, 0.1);
      //text-shadow: 0 10px 15px rgba(0,0,0,0.2);
      pointer-events: none;
      user-select: none;
      z-index: 0;
    }
    textarea {
      width: 100%;
      resize: none;
      min-height: 1.5em;
      box-sizing: border-box;
      border: none;
      outline: none;
      background-color: #f0f0f0;  /* легкий серый для неактивного состояния */
      color: #000;
      padding: 4px;
      border-radius: 4px;
      transition: all 0.2s ease;
    }
    /* При фокусе — делаем фон белым и добавляем тонкую рамку */
    textarea:focus {
      background-color: white;
      border: 1px solid #5468C9;
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
  let topZIndex = 1000;

function attachComponent(comp, x = null, y = null, width = 300) {
  if (attached[comp.id]) return;

  const div = document.createElement("div");
  div.className = "component";
  div.style.top = (y ?? Math.random() * 400) + "px";
  div.style.left = (x ?? Math.random() * 600) + "px";
  div.style.width = width + "px";
  div.setAttribute("data-id", comp.id);

  const headerText = comp.name + " (" + comp.id.slice(0, 6) + ")";
  const header = document.createElement("h4");
  header.textContent = headerText;
  header.style.textAlign = "center";
  div.appendChild(header);

  // ✅ Вычисляем ширину текста
  const span = document.createElement("span");
  span.style.fontFamily = "sans-serif";
  span.style.fontSize = "1em";
  span.style.visibility = "hidden";
  span.style.whiteSpace = "nowrap";
  span.style.position = "absolute";
  span.textContent = headerText;
  document.body.appendChild(span);
  const measuredWidth = span.offsetWidth + 60;  // немного запас под паддинги
  document.body.removeChild(span);

  // Ограничим разумный диапазон
  const clampedWidth = Math.max(200, Math.min(1000, measuredWidth));
  div.style.width = clampedWidth + "px";

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

    const rect = el.getBoundingClientRect();
    const resizeZone = 16;  // нижний правый угол 16x16px
    const isInResizeCorner =
      e.clientX >= rect.right - resizeZone &&
      e.clientY >= rect.bottom - resizeZone;

    if (isInResizeCorner) return;  // 🚫 НЕ начинаем drag

    el.style.zIndex = ++topZIndex;

    el.onclick = function () {
      el.style.zIndex = ++topZIndex;
    };

    let offsetX = e.clientX - el.offsetLeft;
    let offsetY = e.clientY - el.offsetTop;

    // ⛔️ Запретить выделение текста
    document.body.style.userSelect = "none";

    function move(e) {
      el.style.left = (e.clientX - offsetX) + 'px';
      el.style.top = (e.clientY - offsetY) + 'px';
    }

    document.addEventListener('mousemove', move);

    document.addEventListener('mouseup', () => {
      document.removeEventListener('mousemove', move);

      // ✅ Включить обратно выделение текста
      document.body.style.userSelect = "";
    }, { once: true });
  };
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

  div.innerHTML = `
    <h4 style="line-height: 1.2em; text-align: center;">
      ${comp.name} (${id.slice(0,6)})
    </h4>
    <div style="text-align: center; font-weight: normal; font-style: italic; font-size: 0.95em; margin-top: -0.3em; margin-bottom: 0.6em;">
      ${endpoint}
    </div>`;

    const form = document.createElement("form");
    const inputs = comp.io?.[endpoint]?.inputs || {};
    const inputValues = {};

    Object.entries(inputs).forEach(([name, type]) => {
      const label = document.createElement("label");
      label.textContent = name + " (" + type + "):";
      const input = document.createElement("textarea");
      input.rows = 1;
      input.style.overflow = "hidden";

    if (["int", "float", "bool"].includes(type.toLowerCase())) {
      input.style.resize = "none";
    } else {
      input.style.resize = "none";  // чтобы убрать уголок
      input.addEventListener("input", () => autoResize(input));
      autoResize(input);  // сразу подстроим при создании
    }

      //только для составных типов — авторастяжение при вводе
      if (!["int", "float", "bool"].includes(type.toLowerCase())) {
      input.addEventListener("input", () => autoResize(input));
    }

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

    function autoResize(textarea) {
      textarea.style.height = "auto";
      textarea.style.height = textarea.scrollHeight + "px";
    }

  let currentX = 40;
  let currentY = 40;
  const paddingX = 20;
  const paddingY = 20;
  const maxRowWidth = window.innerWidth - 60;

  components.forEach(comp => {
    // Создаем виртуальный div, чтобы измерить ширину компонента
    const testDiv = document.createElement("div");
    testDiv.style.position = "absolute";
    testDiv.style.visibility = "hidden";
    testDiv.style.padding = "10px";
    testDiv.style.font = "bold 1em sans-serif";
    testDiv.style.whiteSpace = "nowrap";
    testDiv.textContent = comp.name + " (" + comp.id.slice(0, 6) + ")";
    document.body.appendChild(testDiv);

    const requiredWidth = testDiv.offsetWidth + 40; // учёт паддингов и границ
    document.body.removeChild(testDiv);

    // Перенос на новую строку при необходимости
    if (currentX + requiredWidth > maxRowWidth) {
      currentX = 40;
      currentY += 180;
    }

    attachComponent(comp, currentX, currentY, requiredWidth);
    currentX += requiredWidth + paddingX;
  });

</script>
</body>
</html>
""")

