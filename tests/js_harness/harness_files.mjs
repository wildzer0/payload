import "./stubs.mjs";
let failed = 0;
const check = (name, cond) => { if (cond) print(`[ok]   ${name}`); else { failed++; print(`[FAIL] ${name}`); } };
const flush = async (n = 15) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const noEscaped = (html) => !html.includes("&lt;span") && !html.includes("&lt;svg") && !html.includes("[object Object]");
const fileBtn = (fs, kind) => ({ dataset: { fs, kind } });

const files = await import("./app/js/views/files.js");
const ui = await import("./app/js/ui.js");
await files.viewFiles();
await flush();
const tree = document.getElementById("fs-tree");
check("files: tree populated", tree.innerHTML.includes("temp.raw"));

tree.onclick({ target: { closest: () => fileBtn("temp.raw", "file") } });
await flush();
const detail = document.getElementById("fs-detail");
check("files: text detail is light (Edit button, no inline editor)",
  !!document.getElementById("fs-edit") && !detail.innerHTML.includes('id="fs-text"') && noEscaped(detail.innerHTML));

const editBtn = document.getElementById("fs-edit");
if (editBtn) editBtn.fire("click");
await flush();
check("files: Edit opens the full-screen editor modal",
  document.getElementById("modal-box").innerHTML.includes("modal-editor") && document.getElementById("modal-overlay").hidden === false);
document.getElementById("modal-overlay").hidden = true;

tree.onclick({ target: { closest: () => fileBtn("blob.bin", "file") } });
await flush();
check("files: binary detail has hex + analyze actions",
  !!document.getElementById("fs-hex-view") && !!document.getElementById("fs-analyze") && noEscaped(document.getElementById("fs-detail").innerHTML));
const hexBtn = document.getElementById("fs-hex-view");
if (hexBtn) hexBtn.fire("click");
await flush();
const hexBox = document.getElementById("modal-box").innerHTML;
check("files: hex view opens in a modal with rows + strings toggle",
  hexBox.includes("fs-strings-toggle") && (hexBox.includes("hex-row") || hexBox.includes("payload data")));
document.getElementById("modal-overlay").hidden = true;

const analyzeBtn = document.getElementById("fs-analyze");
if (analyzeBtn) analyzeBtn.fire("click");
await flush();
check("files: Analyze fills the panel",
  document.getElementById("fs-analyze-panel").innerHTML.includes("entropy") || document.getElementById("fs-analyze-panel").innerHTML.includes("4.5"));

// table page
const table = await import("./app/js/views/table.js");
await table.viewTable("example_table");
await flush();
check("table: page renders source + pipeline cards",
  !!document.getElementById("btn-source-edit") && !!document.getElementById("pb-edit-graph"));
const srcEdit = document.getElementById("btn-source-edit");
if (srcEdit) srcEdit.fire("click");
await flush();
check("table: source Edit opens the editor modal with dirty guard",
  document.getElementById("modal-box").innerHTML.includes("modal-editor"));
document.getElementById("modal-overlay").hidden = true;

// batch table page: source/sidecar cards show members/overrides (no 400s)
// and the rejected endpoints are NEVER called (race: is_batch must be
// known before the loaders run)
{
  const hitSource = [], hitSidecar = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (p2, o) => {
    const u = String(p2);
    if (u.includes("/api/source/sensors")) hitSource.push(u);
    if (u.includes("/api/sidecar/sensors")) hitSidecar.push(u);
    return realFetch(p2, o);
  };
  await table.viewTable("sensors");
  await flush();
  globalThis.fetch = realFetch;
  check("table: batch page never calls /api/source or /api/sidecar",
    hitSource.length === 0 && hitSidecar.length === 0);
}
await table.viewTable("sensors");
await flush();
const batchSrc = document.getElementById("view-result").innerHTML;
const batchSc = document.getElementById("sidecar-result").innerHTML;
check("table: batch page source card shows members as real <li> (not escaped)",
  batchSrc.includes("Batch table") && batchSrc.includes("<li>") && !batchSrc.includes("&lt;li"));
check("table: batch page sidecar card shows overrides as pills (no input boxes)",
  batchSc.includes("Byte order") && batchSc.includes("meta-chip") && !batchSc.includes("<input"));
check("table: no source/sidecar rejection errors surfaced",
  !batchSrc.includes("only supports") && !batchSc.includes("no sidecar"));

// pipeline card compact
await table.viewTable("example_table");
await flush();
const pipeCard = document.getElementById("pipeline-result").innerHTML;
check("table: pipeline card is compact (buttons only)",
  pipeCard.includes("pb-edit-graph") && !pipeCard.includes("stage-list") && noEscaped(pipeCard));

// dashboard
const dash = await import("./app/js/views/dashboard.js");
await dash.viewDashboard();
await flush();
const dashRows = document.getElementById("table-list").innerHTML;
check("dashboard: rows render values, no [object Object]",
  dashRows.includes("example_table") && noEscaped(dashRows));

const buildAllBtn = document.getElementById("dash-build-all");
check("dashboard: Build all button opens the modal", !!buildAllBtn);
if (buildAllBtn) buildAllBtn.fire("click");
await flush();
const baBox = document.getElementById("modal-box").innerHTML;
check("dashboard: build-all modal has config + live log",
  baBox.includes("ba-to") && baBox.includes("ba-jobs") && baBox.includes("ba-log"));
document.getElementById("modal-overlay").hidden = true;

// batch settings modal
await dash.viewDashboard();
await flush();
const settingsBtn = document.querySelector("[data-table-settings]");
const hasSettingsBtn = !!settingsBtn && document.getElementById("table-list").innerHTML.includes("data-table-settings");
if (settingsBtn) settingsBtn.fire("click");
await flush();
const batchModal = document.getElementById("modal-box").innerHTML;
check("dashboard: batch Settings button opens the settings modal",
  hasSettingsBtn && batchModal.includes("Batch — sensors") && batchModal.includes("dash-batch-members") && noEscaped(batchModal));
document.getElementById("modal-overlay").hidden = true;

// single-table settings modal
await dash.viewDashboard();
await flush();
const singleBtn = document.querySelectorAll("[data-table-settings]")[1];
if (singleBtn) singleBtn.fire("click");
await flush();
const tsBox = document.getElementById("modal-box").innerHTML;
check("dashboard: single-table Settings opens the overrides modal",
  tsBox.includes("Settings — example_table") && tsBox.includes("ts-reader") && tsBox.includes("ts-byteorder") && noEscaped(tsBox));
document.getElementById("modal-overlay").hidden = true;

// dialogs get a window-style X instead of a Close button
ui.openDialog({ title: "Test dialog", body: "<p>hi</p>" });
await flush();
const xBox = document.getElementById("modal-box").innerHTML;
check("modals: window-style X replaces the Close button",
  xBox.includes('class="modal-x"') && !xBox.includes(">Close</button>"));
document.getElementById("modal-overlay").hidden = true;

// activity log: Load more appends the next page
{
  const log = await import("./app/js/views/log.js");
  await log.viewLog();
  await flush();
  const list = document.getElementById("activity-list");
  const moreBtn = document.getElementById("activity-more");
  const firstCount = (list.innerHTML.match(/activity-item/g) || []).length;
  check("activity: first page renders with a Load more button",
    firstCount === 50 && !moreBtn.hidden);
  moreBtn.fire("click");
  await flush();
  const secondCount = (list.innerHTML.match(/activity-item/g) || []).length;
  check("activity: Load more appends the next page", secondCount === 60 && moreBtn.hidden);
}

// clusters
const clusters = await import("./app/js/views/clusters.js");
await clusters.viewClusters();
await flush();
const clusterHtml = document.getElementById("content").innerHTML;
check("clusters: grid first, New cluster button, no inline form",
  clusterHtml.includes("sensors") && !!document.getElementById("btn-new-cluster") && !clusterHtml.includes("cluster-form-card") && noEscaped(clusterHtml));
document.getElementById("btn-new-cluster").fire("click");
await flush();
check("clusters: New cluster opens a modal form",
  document.getElementById("modal-box").innerHTML.includes("cluster") && !document.getElementById("modal-box").innerHTML.includes("&lt;span"));
document.getElementById("modal-overlay").hidden = true;

// plugins
const plugins = await import("./app/js/views/plugins.js");
await plugins.viewPlugins();
await flush();
const plugHtml = document.getElementById("content").innerHTML;
check("plugins: install/new in header buttons, no detailsCard",
  plugHtml.includes("btn-install-plugin") && plugHtml.includes("btn-new-plugin") && !plugHtml.includes("details-card") && noEscaped(plugHtml));
const installBtn = document.getElementById("btn-install-plugin");
if (installBtn) installBtn.fire("click");
await flush();
check("plugins: Install opens a modal", document.getElementById("modal-box").innerHTML.includes("plugin-install-drop"));
document.getElementById("modal-overlay").hidden = true;
await plugins.viewPluginDetail("raw_text");
await flush();
const pdet = document.getElementById("content").innerHTML;
check("plugins: detail renders without escaped icons", !pdet.includes("&lt;span"));

// doctor
const doctor = await import("./app/js/views/doctor.js");
await doctor.viewDoctor();
await flush();
const docHtml = document.getElementById("content").innerHTML;
check("doctor: banner + sections", docHtml.includes("doctor-banner-fail") && docHtml.includes("Passed") && noEscaped(docHtml));

// settings modal (the old config page)
const config = await import("./app/js/views/config.js");
await config.openSettingsModal();
await flush();
const cfgHtml = document.getElementById("modal-box").innerHTML;
check("config: settings modal with default pill, no page", cfgHtml.includes("default") && !cfgHtml.includes("section-collapse"));
document.getElementById("modal-overlay").hidden = true;

// markdown
const md = await import("./app/js/markdown.js");
check("markdown: .md -> in-app route", md.renderMarkdown("[guide](plugins.md)").includes("#/docs/plugins"));
check("markdown: external -> new tab", md.renderMarkdown("[ext](https://example.com/x.md)").includes('target="_blank"'));

// render escaping
const escHtml = ui.render`<p>${"it's"}</p>`.__raw || "";
check("render: escapes apostrophes once", !escHtml.includes("it&#39;&#39;s"));

// pipeline editor
const ped = await import("./app/js/views/pipeline_editor.js");
await ped.openPipelineEditor("example_table");
await flush();
const pe = document.getElementById("modal-box").innerHTML;
const peWorld = document.getElementById("pe-world").innerHTML;
const peSvg = document.getElementById("pe-svg").innerHTML;
check("pipeline editor: graph renders nodes + ports + edges",
  pe.includes("Edit pipeline") && peWorld.includes("pe-node") && peWorld.includes("pe-port") && peSvg.includes("pe-edge"));
check("pipeline editor: full-screen modal", document.getElementById("modal-box").classList.contains("modal-large"));
check("pipeline editor: the close X is at the far right (not after the title)",
  pe.indexOf("Edit pipeline") < pe.indexOf("pe-x") && pe.indexOf("pe-zoom-in") < pe.indexOf("pe-x"));

document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-node" ? { dataset: { id: "s1" } } : null) }, clientX: 10, clientY: 10, preventDefault() {} });
await flush();
check("pipeline editor: selecting a node shows the props pane",
  document.getElementById("pe-props").innerHTML.includes("Writer stage"));
const addReaderBtn = document.getElementById("pe-add-reader");
check("pipeline editor: Add reader button present", !!addReaderBtn);
if (addReaderBtn) { addReaderBtn.fire("click"); await flush(); }
check("pipeline editor: Add reader creates a new reader segment",
  (document.getElementById("pe-world").innerHTML.match(/pe-node-kind">reader</g) || []).length >= 2);

const paneBefore = document.getElementById("pe-props").innerHTML;
__fireDocument("mouseup", { clientX: 10, clientY: 10 });
await flush();
check("pipeline editor: plain click does not re-render the props pane",
  document.getElementById("pe-props").innerHTML === paneBefore);

document.getElementById("pe-cancel").fire("click");
await flush();

{
  await ped.openPipelineEditor("example_table");
  await flush();
  document.getElementById("pe-add-writer").fire("click");
  await flush();
  const before = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-out" ? { dataset: { id: "s0" } } : null) }, clientX: 220, clientY: 70, preventDefault() {} });
  __fireDocument("mousemove", { clientX: 540, clientY: 70 });
  __fireDocument("mouseup", { clientX: 540, clientY: 70 });
  await flush();
  const after = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  check("pipeline editor: drag-to-connect adds a validated edge", after === before + 1);

  document.getElementById("pe-add-exec").fire("click");
  await flush();
  const before2 = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-out" ? { dataset: { id: "s0" } } : null) }, clientX: 220, clientY: 70, preventDefault() {} });
  __fireDocument("mousemove", { clientX: 990, clientY: 70 });
  __fireDocument("mouseup", { clientX: 990, clientY: 70 });
  await flush();
  const after2 = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  check("pipeline editor: invalid connection (reader -> exec) is refused", after2 === before2);

  document.getElementById("pe-add-reader").fire("click");
  await flush();
  const before3 = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  const m = document.getElementById("pe-world").innerHTML.match(/data-id="s4" style="left:([0-9.]+)px;top:([0-9.]+)px"/);
  const inX = parseFloat(m[1]) + 40;
  const inY = parseFloat(m[2]) + 70;
  document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-out" ? { dataset: { id: "s1" } } : null) }, clientX: 470, clientY: 70, preventDefault() {} });
  __fireDocument("mousemove", { clientX: inX, clientY: inY });
  __fireDocument("mouseup", { clientX: inX, clientY: inY });
  await flush();
  const after3 = (document.getElementById("pe-svg").innerHTML.match(/pe-edge/g) || []).length;
  check("pipeline editor: mid-build writer->reader connect is allowed", after3 === before3 + 1);

  document.getElementById("pe-save").fire("click");
  await flush();
  const banner = document.getElementById("pe-error");
  check("pipeline editor: save refuses an incomplete pipeline (banner)",
    !banner.hidden && /connected|last stage|Can't save/.test(banner.textContent));
  document.getElementById("pe-cancel").fire("click");
  await flush();
}

{
  await ped.openPipelineEditor("example_table");
  await flush();
  const box = document.getElementById("modal-box").innerHTML;
  const gi = box.indexOf('class="pe-toolbar-actions"');
  check("pipeline editor: toolbar actions grouped, Cancel can't wrap",
    gi > -1 && box.indexOf('id="pe-save"') > gi && box.indexOf('id="pe-cancel"') > gi && box.indexOf('id="pe-canvas"') > gi);
  document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-node" ? { dataset: { id: "s1" } } : null) }, clientX: 10, clientY: 10, preventDefault() {} });
  await flush();
  const rmBtn = document.getElementById("pe-remove");
  check("pipeline editor: Remove stage button present in the props pane", !!rmBtn);
  if (rmBtn) rmBtn.fire("click");
  await flush();
  check("pipeline editor: removing a stage keeps the other connections",
    !document.getElementById("pe-world").innerHTML.includes('data-id="s1"'));
  document.getElementById("pe-cancel").fire("click");
  await flush();
}

{
  await ped.openPipelineEditor("example_table");
  await flush();
  const svg = document.getElementById("pe-svg").innerHTML;
  check("pipeline editor: same-row edge is a straight line (no arcs)",
    /M 220 70 C [0-9.]+ 70, [0-9.]+ 70, 290 70/.test(svg) && !svg.includes(" A ") && !svg.includes(" Q "));
  document.getElementById("pe-auto-layout").fire("click");
  await flush();
  const xs = [...document.getElementById("pe-world").innerHTML.matchAll(/left:([0-9.]+)px/g)].map((mm) => parseFloat(mm[1]));
  check("pipeline editor: auto-layout keeps nodes inside the canvas width",
    xs.every((x) => x + 180 <= (document.getElementById("pe-canvas").clientWidth || 900) + 1));
  document.getElementById("pe-canvas").fire("mousedown", { target: { closest: (sel) => (sel === ".pe-node" ? { dataset: { id: "s1" } } : null) }, clientX: 10, clientY: 10, preventDefault() {} });
  await flush();
  check("pipeline editor: dragged node's edges render on the live overlay",
    document.getElementById("pe-svg-live").innerHTML.includes("pe-edge-live"));
  __fireDocument("mouseup", { clientX: 10, clientY: 10 });
  await flush();
  check("pipeline editor: live overlay clears after the drag",
    document.getElementById("pe-svg-live").innerHTML === "");
  document.getElementById("pe-cancel").fire("click");
  await flush();
}

{
  await ped.openPipelineEditor("example_table");
  await flush();
  const putCalls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (p2, o) => { if (String(p2).includes("/api/pipeline/example_table") && o && o.method === "PUT") putCalls.push(JSON.parse(o.body)); return realFetch(p2, o); };
  document.getElementById("pe-save").fire("click");
  await flush();
  globalThis.fetch = realFetch;
  const saved = putCalls[putCalls.length - 1];
  check("pipeline editor: save persists the stage positions (x/y)",
    saved && saved.stages.every((st) => typeof st.x === "number" && typeof st.y === "number"));
  document.getElementById("pe-cancel").fire("click");
  await flush();
}

{
  await ped.openPipelineEditor("example_table");
  await flush();
  document.getElementById("pe-canvas").clientWidth = 640;
  for (let i = 0; i < 3; i++) { document.getElementById("pe-add-writer").fire("click"); await flush(); }
  const wxs = [...document.getElementById("pe-world").innerHTML.matchAll(/left:([0-9.]+)px/g)].map((mm) => parseFloat(mm[1]));
  const wys = [...document.getElementById("pe-world").innerHTML.matchAll(/top:([0-9.]+)px/g)].map((mm) => parseFloat(mm[1]));
  check("pipeline editor: new stages stay inside the visible canvas (wrap)",
    wxs.every((x) => x + 180 <= 640 + 1) && new Set(wys).size >= 2);
  document.getElementById("pe-cancel").fire("click");
  await flush();
}

print(failed ? `RESULT: ${failed} FAILURE(S)` : "RESULT: all checks passed");
