from textwrap import dedent

connection_support = dedent("""
<style>
  svg.connections {
    position: absolute;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    pointer-events: none;
    z-index: 0;
  }
  line.connection-line {
    stroke: #333;
    stroke-width: 2;
    marker-end: url(#arrowhead);
  }
</style>

<svg class="connections">
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="7" 
      refX="10" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#333" />
    </marker>
  </defs>
</svg>

<script>
let selectedComponent = null;
const svg = document.querySelector("svg.connections");

function getCenter(el) {
  const rect = el.getBoundingClientRect();
  return {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  };
}

document.addEventListener("dblclick", (e) => {
  const target = e.target.closest(".component");

  if (!target) {
    selectedComponent = null;
    return;
  }

  if (!selectedComponent) {
    selectedComponent = target;
    target.style.outline = "3px dashed red";
  } else if (selectedComponent !== target) {
    drawConnection(selectedComponent, target);
    selectedComponent.style.outline = "";
    selectedComponent = null;
  }
});

function drawConnection(fromEl, toEl) {
  const from = getCenter(fromEl);
  const to = getCenter(toEl);

  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  line.setAttribute("x1", from.x);
  line.setAttribute("y1", from.y);
  line.setAttribute("x2", to.x);
  line.setAttribute("y2", to.y);
  line.classList.add("connection-line");
  svg.appendChild(line);
}
</script>
""")

