/* Graphical pipeline editor (full-screen modal): the pipeline as a
 * node graph — reader -> exec stages -> writers. Nodes are laid out
 * automatically by depth (auto-layout, no free canvas), edges connect
 * by dragging from an output port to an input port, and a connection
 * is refused when it would break the pipeline rules (no writer as a
 * source, no reader as a target, one writer per type). Saving writes
 * the explicit [pipeline] stages via PUT /api/pipeline/<name>, which
 * validates again server-side. */
"use strict";

import { escapeHtml, iconSpan, toast, toastError } from "../ui.js";
import { api, getPlugins } from "../api.js";

const NODE_W = 180;
const NODE_H = 60;
const KIND_ICON = { reader: "file", exec: "play", writer: "save" };

// free-canvas graph model: nodes placed by the USER (world coordinates,
// no auto-layout), edges drawn by drag. Each node has at most one
// incoming edge (so the graph is a set of paths + terminal fan-outs,
// which always yields a unique reading order); a reader may feed
// several writers (fan-out). Every connection is validated, and Save
// re-derives the order and runs the full alternation rules.

export async function openPipelineEditor(name) {
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  const [p, plugins] = await Promise.all([
    api("/api/pipeline/" + encodeURIComponent(name)),
    getPlugins(),
  ]);
  const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
  const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);

  let seq = 0;
  const state = {
    nodes: p.stages.map((s) => {
      const base = s.kind === "exec"
        ? { kind: "exec", command: s.command, on_error: s.on_error || "fail", output_extension: s.output_extension || "" }
        : { kind: s.kind, name: s.name };
      // restore the saved canvas position; a fresh stage (no x/y yet)
      // gets 0/0 and is staggered by the initial layout below
      return { id: "s" + (seq++), ...base, x: Number.isFinite(s.x) ? s.x : 0, y: Number.isFinite(s.y) ? s.y : 0 };
    }),
    edges: [],       // [{from, to}] — data flow between nodes
    selected: null,
    view: { x: 40, y: 40, scale: 1 }, // pan + zoom (screen = world*scale + view)
  };
  let drag = null;   // {type:"node"|"edge", ...}
  let pan = null;    // {startX, startY, viewX, viewY}
  let canvasEl = null;
  let worldEl = null;

  const getNode = (id) => state.nodes.find((n) => n.id === id);
  const incoming = (id) => state.edges.find((e) => e.to === id);
  const outgoing = (id) => state.edges.filter((e) => e.from === id);

  // initial auto-arrangement (a left-to-right flow) + the default edges
  (function init() {
    let x = 0;
    const hasSavedLayout = p.stages.some((st) => Number.isFinite(st.x) || Number.isFinite(st.y));
    if (!hasSavedLayout) {
      // first edit of an implicitly-resolved pipeline: stagger the stages
      state.nodes.forEach((n) => { n.x = x; n.y = 0; x += NODE_W + 70; });
    }
    let i = 0;
    while (i < state.nodes.length - 1) {
      const a = state.nodes[i], b = state.nodes[i + 1];
      if (a.kind === "reader" && b.kind === "writer") {
        let j = i + 1;
        while (j < state.nodes.length && state.nodes[j].kind === "writer") { state.edges.push({ from: a.id, to: state.nodes[j].id }); j++; }
        if (j < state.nodes.length) state.edges.push({ from: state.nodes[j - 1].id, to: state.nodes[j].id });
        i = j;
      } else {
        state.edges.push({ from: a.id, to: b.id });
        i++;
      }
    }
  })();

  /* ---------- validation ---------- */

  const validateSequence = (stages) => {
    if (stages.length < 2) return "the pipeline needs at least a reader and a writer";
    if (stages[0].kind !== "reader") return "the first stage must be a reader";
    for (let i = 0; i < stages.length; i++) {
      const st = stages[i];
      if (st.kind === "reader") {
        if (i + 1 >= stages.length) return "a reader can't be the pipeline's last stage";
        if (stages[i + 1].kind !== "writer") return `the reader '${st.name}' must be immediately followed by a writer`;
      } else if (st.kind === "writer") {
        let j = i; const names = new Set();
        while (j < stages.length && stages[j].kind === "writer") {
          if (names.has(stages[j].name)) return `writer '${stages[j].name}' is used twice in the same fan-out group`;
          names.add(stages[j].name); j++;
        }
        if (j - i >= 2 && j < stages.length) return "a fan-out (2+ writers in a row) must be the last group — no reader/exec can follow it";
        i = j - 1;
      } else if (st.kind === "exec" && i === stages.length - 1 && !st.output_extension) {
        return "the last exec stage must declare an output_extension";
      }
    }
    return null;
  };

  // with at most one incoming edge per node the graph is a set of paths
  // plus terminal fan-outs: walk it to get the unique reading order
  const orderedNodes = () => {
    const inCount = {}; state.nodes.forEach((n) => { inCount[n.id] = 0; });
    state.edges.forEach((e) => { inCount[e.to] = (inCount[e.to] || 0) + 1; });
    const start = state.nodes.find((n) => n.kind === "reader" && !inCount[n.id]);
    const order = [];
    const seen = new Set();
    let cur = start;
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      order.push(cur);
      const outs = outgoing(cur.id);
      if (outs.length === 0) break;
      if (outs.length === 1) { cur = getNode(outs[0].to); continue; }
      outs.forEach((e) => { if (!seen.has(e.to)) { seen.add(e.to); order.push(getNode(e.to)); } });
      break; // fan-out is terminal
    }
    const disconnected = state.nodes.some((n) => !seen.has(n.id));
    return { order, disconnected };
  };

  // LOCAL connection rules only: a connection is refused when the two
  // stages can never be adjacent in ANY valid pipeline. Completeness
  // (first/last stage, fan-out placement, ...) is deliberately checked
  // on SAVE — the user may be mid-construction (e.g. a just-added
  // reader that will get its writer next).
  const canConnect = (from, to) => {
    if (!from || !to) return "missing stage";
    if (from.id === to.id) return "a stage can't feed itself";
    if (state.edges.some((e) => e.from === from.id && e.to === to.id)) return "these two stages are already connected";
    // a reader's output is in-memory data: it can only be materialized
    // by a writer (no reader->exec, no reader->reader)
    if (from.kind === "reader" && to.kind !== "writer") return "a reader must feed a writer — it produces in-memory data, not files";
    // a reader starts a new segment: it can be fed by a writer or an
    // exec (multiple readers after a single writer or exec)
    if (to.kind === "reader" && from.kind !== "writer" && from.kind !== "exec") return "a reader can only be fed by a writer or an exec (a new segment starts there)";
    // a writer feeding another writer extends a fan-out: the writer
    // name must stay unique across the whole pipeline
    if (from.kind === "writer" && to.kind === "writer") {
      const dup = state.nodes.find((n) => n.kind === "writer" && n.name === to.name && n.id !== to.id);
      if (dup) return `writer '${to.name}' is already used in the pipeline`;
    }
    return null;
  };

  /* ---------- rendering ---------- */

  const stageLabel = (n) => (n.kind === "exec" ? n.command : n.name);

  const nodeHtml = (n) => {
    const sel = state.selected === n.id ? " selected" : "";
    const kindCls = n.kind === "reader" ? "pe-reader" : n.kind === "writer" ? "pe-writer" : "pe-exec";
    return `
      <div class="pe-node ${kindCls}${sel}" id="pe-node-${n.id}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px">
        <span class="pe-port pe-in" data-id="${n.id}"></span>
        <span class="pe-node-icon">${iconSpan(KIND_ICON[n.kind] || "box")}</span>
        <div class="pe-node-body">
          <div class="pe-node-kind">${n.kind}</div>
          <div class="pe-node-label" title="${escapeHtml(stageLabel(n))}">${escapeHtml(stageLabel(n))}</div>
        </div>
        <span class="pe-port pe-out" data-id="${n.id}"></span>
      </div>`;
  };

  // State-of-the-art edge routing (Draw.io / FigJam style): orthogonal
  // path with ROUNDED corners and a minimum stub out of each port. The
  // elbow goes to the right when the target is ahead, to the left when
  // it's behind, so the edge never crosses the source node.
  // Industry-standard smooth edges (React Flow / Airflow / Dagster
  // style): a single cubic bezier with HORIZONTAL tangents at both
  // ports. Forward edges sweep gently; backward edges loop around the
  // source; same-row edges collapse to a straight line. No corners, no
  // segments — nothing left to look distorted.
  const edgePath = (a, b) => {
    const bend = Math.max(24, Math.min(160, Math.abs(b.x - a.x) * 0.35));
    return `M ${a.x} ${a.y} C ${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}`;
  };

  const portScreen = (node, side) => ({
    x: node.x * state.view.scale + state.view.x + (side === "out" ? NODE_W * state.view.scale : 0),
    y: node.y * state.view.scale + state.view.y + (NODE_H * state.view.scale) / 2,
  });

  const render = () => {
    if (!canvasEl) return;
    clearSaveError();
    const { scale, x: vx, y: vy } = state.view;
    const svgEdges = state.edges.map((e) => {
      const p1 = portScreen(getNode(e.from), "out");
      const p2 = portScreen(getNode(e.to), "in");
      return `<path d="${edgePath(p1, p2)}" class="pe-edge"></path>`;
    }).join("");
    const dragEdge = drag && drag.type === "edge"
      ? `<path d="${edgePath(portScreen(getNode(drag.fromId), "out"), drag.to)}" class="pe-edge pe-edge-drag"></path>`
      : "";
    const svgEl = document.getElementById("pe-svg");
    const liveEl = document.getElementById("pe-svg-live");
    const w = canvasEl.clientWidth || 1200;
    const h = canvasEl.clientHeight || 700;
    if (svgEl) {
      svgEl.setAttribute("width", w);
      svgEl.setAttribute("height", h);
      svgEl.innerHTML = svgEdges;
    }
    // while dragging a node, its own edges (and the in-progress edge)
    // are drawn on the overlay ABOVE the nodes, so the live connection
    // is never hidden behind another stage
    const liveEdges = drag && drag.type === "node"
      ? state.edges.filter((e) => e.from === drag.node.id || e.to === drag.node.id).map((e) => {
        const p1 = portScreen(getNode(e.from), "out");
        const p2 = portScreen(getNode(e.to), "in");
        return `<path d="${edgePath(p1, p2)}" class="pe-edge pe-edge-live"></path>`;
      }).join("")
      : "";
    if (liveEl) {
      liveEl.setAttribute("width", w);
      liveEl.setAttribute("height", h);
      liveEl.innerHTML = liveEdges + dragEdge;
    }
    if (worldEl) {
      worldEl.innerHTML = state.nodes.map((n) => nodeHtml(n)).join("");
      worldEl.style.transform = `translate(${vx}px, ${vy}px) scale(${scale})`;
    }
    renderProps();
  };

  const renderProps = () => {
    const pane = document.getElementById("pe-props");
    if (!pane) return;
    const n = state.selected ? getNode(state.selected) : null;
    if (!n) { pane.innerHTML = '<p class="empty-state m-0">Select a stage to edit its properties.</p>'; return; }
    if (n.kind === "exec") {
      pane.innerHTML = `
        <h4 class="pe-props-title">Exec stage</h4>
        <div class="field"><label>Command</label><input id="pe-cmd" class="mono" value="${escapeHtml(n.command)}"></div>
        <div class="field"><label>On error</label>
          <select id="pe-onerr"><option value="fail"${n.on_error === "fail" ? " selected" : ""}>fail</option><option value="warn"${n.on_error === "warn" ? " selected" : ""}>warn</option><option value="ignore"${n.on_error === "ignore" ? " selected" : ""}>ignore</option></select>
        </div>
        <div class="field"><label>Output extension</label><input id="pe-ext" class="mono" value="${escapeHtml(n.output_extension)}" placeholder="e.g. .bin"></div>
        <button class="danger" id="pe-remove">${iconSpan("trash")}Remove stage</button>
      `;
      document.getElementById("pe-cmd").addEventListener("input", (ev) => { n.command = ev.target.value; refreshLabel(n); });
      document.getElementById("pe-onerr").addEventListener("change", (ev) => { n.on_error = ev.target.value; });
      document.getElementById("pe-ext").addEventListener("input", (ev) => { n.output_extension = ev.target.value; });
      document.getElementById("pe-remove").onclick = removeSelected;
    } else {
      const names = n.kind === "reader" ? readerNames : writerNames;
      const taken = n.kind === "writer"
        ? new Set(state.nodes.filter((x) => x.kind === "writer" && x.id !== n.id).map((x) => x.name))
        : new Set();
      pane.innerHTML = `
        <h4 class="pe-props-title">${n.kind === "reader" ? "Reader stage" : "Writer stage"}</h4>
        <div class="field"><label>${n.kind === "reader" ? "Reader" : "Writer"}</label>
          <select id="pe-name">${names.map((nm) => `<option value="${escapeHtml(nm)}"${nm === n.name ? " selected" : ""}${taken.has(nm) ? " disabled" : ""}>${escapeHtml(nm)}${taken.has(nm) ? " (in use)" : ""}</option>`).join("")}</select>
        </div>
        <button class="danger" id="pe-remove">${iconSpan("trash")}Remove stage</button>
      `;
      document.getElementById("pe-remove").onclick = removeSelected;
      const sel = document.getElementById("pe-name");
      if (sel) sel.addEventListener("change", (ev) => { n.name = ev.target.value; refreshLabel(n); });
    }
    const rm = document.getElementById("pe-remove");
    if (rm) rm.onclick = () => {
      state.nodes = state.nodes.filter((x) => x.id !== n.id);
      state.edges = state.edges.filter((e) => e.from !== n.id && e.to !== n.id);
      state.selected = null;
      render();
    };
  };

  const refreshLabel = (n) => {
    const node = document.getElementById("pe-node-" + n.id);
    if (node) {
      const label = node.querySelector(".pe-node-label");
      if (label) { label.textContent = stageLabel(n); label.title = stageLabel(n); }
    }
  };

  /* ---------- interactions ---------- */

  const toWorld = (clientX, clientY) => {
    const rect = canvasEl.getBoundingClientRect();
    return { x: (clientX - rect.left - state.view.x) / state.view.scale, y: (clientY - rect.top - state.view.y) / state.view.scale };
  };

  const hitInPort = (clientX, clientY) => {
    for (const n of state.nodes) {
      const p = portScreen(n, "in");
      if (Math.abs(clientX - p.x) < 24 && Math.abs(clientY - p.y) < 24) return n;
    }
    return null;
  };

  const onMouseMove = (ev) => {
    if (drag && drag.type === "edge") {
      const rect = canvasEl.getBoundingClientRect();
      drag.to = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
      render();
      return;
    }
    if (drag && drag.type === "node") {
      const w = toWorld(ev.clientX, ev.clientY);
      drag.node.x = w.x - drag.ox;
      drag.node.y = w.y - drag.oy;
      render();
      return;
    }
    if (pan) {
      state.view.x = pan.viewX + (ev.clientX - pan.startX);
      state.view.y = pan.viewY + (ev.clientY - pan.startY);
      render();
    }
  };

  const onMouseUp = (ev) => {
    if (drag && drag.type === "edge") {
      const rect = canvasEl.getBoundingClientRect();
      const to = hitInPort(ev.clientX - rect.left, ev.clientY - rect.top);
      const from = getNode(drag.fromId);
      if (to && from) {
        const error = canConnect(from, to);
        if (error) toast(`Can't connect: ${error}`, "warn");
        else {
          state.edges = state.edges.filter((e) => e.to !== to.id).concat([{ from: from.id, to: to.id }]);
        }
      }
      drag = null;
      render();
      return;
    }
    if (drag && drag.type === "node") {
      // a plain click (no movement) must NOT re-render: re-rendering the
      // props pane replaces its buttons BEFORE the click event fires,
      // so e.g. "Remove stage" would never run. Only the live edge
      // overlay (drawn above the nodes while dragging) is cleared.
      const moved = drag.node.x !== drag.startX || drag.node.y !== drag.startY;
      drag = null;
      pan = null;
      if (moved) {
        render();
      } else {
        const liveEl = document.getElementById("pe-svg-live");
        if (liveEl) liveEl.innerHTML = "";
      }
      return;
    }
    drag = null;
    pan = null;
  };

  // Removal policy: only the edges touching the removed stage disappear —
  // every other connection and every node position stays untouched. A
  // broken pipeline is reported on SAVE (banner), not at removal time.
  const removeSelected = () => {
    if (!state.selected) return;
    const n = getNode(state.selected);
    if (!n) return;
    state.nodes = state.nodes.filter((x) => x.id !== n.id);
    state.edges = state.edges.filter((e) => e.from !== n.id && e.to !== n.id);
    state.selected = null;
    render();
  };

  const onKey = (ev) => {
    if (ev.key === "Escape") close();
    if ((ev.key === "Delete" || ev.key === "Backspace") && state.selected) {
      ev.preventDefault();
      removeSelected();
      return;
    }
    if (ev.key === "=" || ev.key === "+") { state.view.scale = Math.min(2.5, state.view.scale * 1.15); render(); }
    if (ev.key === "-") { state.view.scale = Math.max(0.3, state.view.scale / 1.15); render(); }
    if (ev.key === "l" || ev.key === "L") autoLayout();
  };

  const close = () => {
    overlay.hidden = true;
    box.classList.remove("modal-large");
    box.innerHTML = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    document.removeEventListener("keydown", onKey);
    canvasEl.removeEventListener("mousedown", onCanvasMouseDown);
    canvasEl.removeEventListener("wheel", onCanvasWheel);
  };

  /* ---------- modal ---------- */

  box.classList.add("modal-large");
  box.innerHTML = `
    <div class="pipe-editor">
      <div class="pipe-editor-toolbar">
        <h3 class="m-0">Edit pipeline — ${escapeHtml(name)}</h3>
        <span class="pipe-zoom">
          <button id="pe-zoom-out" title="Zoom out">−</button>
          <span class="mono" id="pe-zoom-label">100%</span>
          <button id="pe-zoom-in" title="Zoom in">+</button>
          <button id="pe-zoom-fit" title="Reset view">⤢</button>
        </span>
        <button id="pe-auto-layout" title="Auto layout — arrange the stages to fit the canvas">${iconSpan("layers")}Auto layout</button>
        <span class="flex-1"></span>
        <span class="pe-toolbar-actions">
          <button id="pe-add-reader" title="Add a reader stage">${iconSpan("plus")}Reader</button>
          <button id="pe-add-exec" title="Add an exec stage">${iconSpan("plus")}Exec</button>
          <button id="pe-add-writer" title="Add a writer stage">${iconSpan("plus")}Writer</button>
          <button class="primary" id="pe-save">${iconSpan("save")}Save</button>
          <button id="pe-cancel">Cancel</button>
        </span>
      </div>
      <div id="pe-error" class="pe-error" hidden></div>
      <div class="pipe-editor-main">
        <div class="pe-canvas" id="pe-canvas">
          <svg class="pe-svg" id="pe-svg" width="200" height="200"></svg>
          <div class="pe-world" id="pe-world"></div>
          <svg class="pe-svg pe-svg-live" id="pe-svg-live" width="200" height="200"></svg>
        </div>
        <div class="pe-props" id="pe-props"></div>
      </div>
      <p class="subtitle m-0">Drag a stage's output port (right) to another stage's input port (left) to connect them — invalid connections are refused. Drag a stage to move it, drag the empty canvas to pan, scroll or +/- to zoom, click a stage to edit it, Delete removes it.</p>
    </div>
  `;
  overlay.hidden = false;
  canvasEl = document.getElementById("pe-canvas");
  worldEl = document.getElementById("pe-world");
  render();

  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
  document.addEventListener("keydown", onKey);

  const onCanvasMouseDown = (ev) => {
    const out = ev.target.closest && ev.target.closest(".pe-out");
    if (out) {
      ev.preventDefault();
      const from = getNode(out.dataset.id);
      if (!from) return; // stale handler from a closed editor: ignore
      const rect = canvasEl.getBoundingClientRect();
      drag = { type: "edge", fromId: out.dataset.id, to: { x: ev.clientX - rect.left, y: ev.clientY - rect.top } };
      render();
      return;
    }
    const node = ev.target.closest && ev.target.closest(".pe-node");
    if (node) {
      ev.preventDefault();
      const n = getNode(node.dataset.id);
      if (!n) return; // stale handler from a closed editor: ignore
      state.selected = node.dataset.id;
      const w = toWorld(ev.clientX, ev.clientY);
      drag = { type: "node", node: n, ox: w.x - n.x, oy: w.y - n.y, startX: n.x, startY: n.y };
      render();
      return;
    }
    // empty canvas: pan
    pan = { startX: ev.clientX, startY: ev.clientY, viewX: state.view.x, viewY: state.view.y };
  };

  const onCanvasWheel = (ev) => {
    ev.preventDefault();
    const rect = canvasEl.getBoundingClientRect();
    const cx = ev.clientX - rect.left, cy = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.12 : 0.89;
    const ns = Math.min(2.5, Math.max(0.3, state.view.scale * factor));
    state.view.x = cx - ((cx - state.view.x) / state.view.scale) * ns;
    state.view.y = cy - ((cy - state.view.y) / state.view.scale) * ns;
    state.view.scale = ns;
    render();
  };
  canvasEl.addEventListener("mousedown", onCanvasMouseDown);
  canvasEl.addEventListener("wheel", onCanvasWheel, { passive: false });

  // Auto layout: sequence the pipeline (orderedNodes) and lay the
  // stages out in a grid that fits the CURRENT canvas size — the number
  // of columns comes from the canvas width, rows are centered vertically.
  const autoLayout = () => {
    const { order, disconnected } = orderedNodes();
    if (disconnected) { toast("Some stage isn't connected — auto-layout needs the full pipeline", "warn"); return; }
    const cw = canvasEl.clientWidth || 900;
    const ch = canvasEl.clientHeight || 600;
    const GAP_X = 70;
    const GAP_Y = 64;
    const cols = Math.max(1, Math.floor((cw + GAP_X) / (NODE_W + GAP_X)));
    const rows = Math.ceil(order.length / cols);
    const offY = Math.max(0, Math.floor((ch - rows * GAP_Y) / 2));
    order.forEach((n, i) => {
      n.x = (i % cols) * (NODE_W + GAP_X);
      n.y = Math.floor(i / cols) * GAP_Y + offY;
    });
    state.view = { x: 40, y: 40, scale: 1 };
    render();
  };

  document.getElementById("pe-auto-layout").onclick = autoLayout;

  const zoomIn = document.getElementById("pe-zoom-in");
  const zoomOut = document.getElementById("pe-zoom-out");
  const zoomLabel = document.getElementById("pe-zoom-label");
  const applyZoom = (f) => {
    state.view.scale = Math.min(2.5, Math.max(0.3, state.view.scale * f));
    if (zoomLabel) zoomLabel.textContent = Math.round(state.view.scale * 100) + "%";
    render();
  };
  if (zoomIn) zoomIn.onclick = () => applyZoom(1.2);
  if (zoomOut) zoomOut.onclick = () => applyZoom(1 / 1.2);
  const zoomFit = document.getElementById("pe-zoom-fit");
  if (zoomFit) zoomFit.onclick = () => {
    state.view = { x: 40, y: 40, scale: 1 };
    if (zoomLabel) zoomLabel.textContent = "100%";
    render();
  };

  const addNode = (kind, name, command) => {
    // the canvas can't scroll horizontally: a new stage must land INSIDE
    // the visible area — right after the rightmost visible node, wrapping
    // to a new row when the current one is full
    const cw = canvasEl.clientWidth || 1200;
    const { scale, x: vx } = state.view;
    const visibleRight = (cw - vx) / scale;
    const visible = state.nodes.filter((n) => n.x + NODE_W <= visibleRight + 1);
    const anchor = visible.length ? visible.reduce((m, n) => (n.x > m.x ? n : m)) : null;
    let x = anchor ? anchor.x + NODE_W + 70 : 0;
    let y = anchor ? anchor.y : 0;
    if (!anchor || x + NODE_W > visibleRight + 1) {
      x = 0;          // current row is full: wrap to the next row
      y = (anchor ? anchor.y : 0) + 64;
    }
    const node = kind === "exec"
      ? { id: "s" + (seq++), kind, command: command || "your-command", on_error: "fail", output_extension: "", x, y }
      : { id: "s" + (seq++), kind, name, x, y };
    state.nodes.push(node);
    state.selected = node.id;
    render();
  };

  document.getElementById("pe-add-reader").onclick = () => {
    const used = new Set(state.nodes.filter((n) => n.kind === "reader").map((n) => n.name));
    const rName = readerNames.find((nm) => !used.has(nm)) || readerNames[0];
    if (!rName) { toast("No reader plugins available", "warn"); return; }
    addNode("reader", rName);
  };

  document.getElementById("pe-add-exec").onclick = () => addNode("exec");

  document.getElementById("pe-add-writer").onclick = () => {
    const used = new Set(state.nodes.filter((n) => n.kind === "writer").map((n) => n.name));
    const free = writerNames.filter((nm) => !used.has(nm));
    if (!free.length) { toast("All writers are already in the pipeline", "warn"); return; }
    addNode("writer", free[0]);
  };

  // function declarations so render() (defined earlier) can call them
  function peErrorEl() { return document.getElementById("pe-error"); }
  function showSaveError(msg) {
    const el = peErrorEl();
    if (el) { el.textContent = msg; el.hidden = false; }
  }
  function clearSaveError() {
    const el = peErrorEl();
    if (el) { el.textContent = ""; el.hidden = true; }
  }

  document.getElementById("pe-save").onclick = async () => {
    const { order, disconnected } = orderedNodes();
    if (disconnected) { showSaveError("Some stage isn't connected to the pipeline"); return; }
    const error = validateSequence(order);
    if (error) { showSaveError(`Can't save: ${error}`); return; }
    clearSaveError();
    const stages = order.map((n) => {
      const st = n.kind === "exec"
        ? { type: "exec", command: n.command, on_error: n.on_error, output_extension: n.output_extension }
        : { type: n.kind, name: n.name };
      // persist the canvas position so the layout survives reopen
      st.x = n.x;
      st.y = n.y;
      return st;
    });
    const btn = document.getElementById("pe-save");
    btn.disabled = true;
    try {
      await api("/api/pipeline/" + encodeURIComponent(name), { method: "PUT", body: { stages } });
      toast(`Pipeline of '${name}' saved`, "ok");
      close();
      document.dispatchEvent(new CustomEvent("pipeline-saved"));
    } catch (e) {
      toastError(e);
      btn.disabled = false;
    }
  };

  document.getElementById("pe-cancel").onclick = close;
}
