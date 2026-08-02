/* Plugins views (routes "/plugins", "/plugin/<name>",
 * "/local-plugin/<file>"): the filterable plugin grid, plugin detail
 * with conformance validation, install/new-plugin cards, and the
 * CodeMirror-based editor for local plugins in plugins/. Split out of
 * the former single-file app.js — no behavior change. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, detailsCard, pageHeader, statusPill, metaChip,
  formatDescription, val, debounce, registerDirtyGuard, clearDirtyGuards,
} from "../ui.js";
import { api, apiUpload, invalidatePluginsCache } from "../api.js";

const PLUGIN_KIND_META = {
  reader: { title: "Reader", icon: "book" },
  writer: { title: "Writer", icon: "save" },
  doctor_check: { title: "Doctor check", icon: "check" },
};

function _pluginCardHtml(p) {
  const meta = PLUGIN_KIND_META[p.kind];
  const extPills = p.extensions.map((e) => `<span class="pill pill-dim mono">${escapeHtml(e)}</span>`).join("");
  return `
    <a class="plugin-card${p.installed ? " plugin-card-installed" : ""}" href="#/plugin/${encodeURIComponent(p.name)}">
      <div class="plugin-card-kind">${iconSpan(meta.icon)}${meta.title}</div>
      <div class="plugin-card-name">${escapeHtml(p.name)}</div>
      <div class="plugin-card-meta">${extPills}<span class="pill pill-dim">v${escapeHtml(p.api_version)}</span></div>
    </a>`;
}

// Used to be 3 boxes (one per kind), each with its own built-ins
// <details> — expanding one grew only that box, so the row never lined
// up (the box's size wasn't fixed, it tracked whatever was open). A
// single flat grid with a filter bar reflows as one predictable unit
// instead, the same way a search result list does, and no box is ever
// bigger or smaller than its neighbor because there ARE no neighbors —
// just one grid.
function _pluginGridHtml(plugins, kindFilter, showInstalled) {
  const filtered = plugins.filter((p) => (!kindFilter || p.kind === kindFilter) && (showInstalled || !p.installed));
  if (filtered.length === 0) {
    return '<p class="empty-state">No plugin matches this filter.</p>';
  }
  return `<div class="plugin-grid">${filtered.map(_pluginCardHtml).join("")}</div>`;
}

function _pluginToolbarHtml(plugins) {
  const counts = { "": plugins.length };
  for (const kind of Object.keys(PLUGIN_KIND_META)) counts[kind] = plugins.filter((p) => p.kind === kind).length;
  const installedCount = plugins.filter((p) => p.installed).length;
  const chip = (kind, label) => `
    <button type="button" class="toggle-chip plugin-filter-chip${kind === "" ? " active" : ""}" data-kind-filter="${kind}">
      ${escapeHtml(label)} <span class="pill pill-dim">${counts[kind]}</span>
    </button>`;
  return `
    <div class="plugin-toolbar">
      <div class="toggle-chip-row m-0" id="plugin-kind-filters">
        ${chip("", "All")}
        ${Object.entries(PLUGIN_KIND_META).map(([kind, meta]) => chip(kind, meta.title)).join("")}
      </div>
      <label class="toggle-chip">
        <input type="checkbox" id="plugin-show-installed">
        Show installed (pip) plugins <span class="pill pill-dim">${installedCount}</span>
      </label>
    </div>`;
}

async function viewPlugins() {
  const [r, local] = await Promise.all([api("/api/plugins"), api("/api/local-plugins")]);
  let kindFilter = "";

  let showInstalled = localStorage.getItem("showInstalledPlugins") === "true";

  const localRows = local.files.map((f) => render`
    <div class="local-plugin-row">
      <span class="mono">${f.filename}</span>
      <span>${raw(f.kinds.length ? f.kinds.map((k) => `<span class="pill pill-dim">${escapeHtml(k)}</span>`).join("") : '<span class="pill pill-fail">not loadable</span>')}${raw(f.stub_methods.length ? `<span class="pill pill-warn" title="${escapeHtml(`Still to implement: ${f.stub_methods.join(", ")}`)}">not implemented</span>` : "")}</span>
      <div class="local-plugin-row-actions">
        <a class="btn" href="#/local-plugin/${encodeURIComponent(f.filename)}">${icon("book")}Open in editor</a>
        <button class="danger icon-only" data-del-local-plugin="${f.filename}" title="Delete local plugin">${icon("trash")}</button>
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Plugins"))}
    ${raw(_pluginToolbarHtml(r.plugins))}
    <div id="plugin-grid-wrap">${raw(_pluginGridHtml(r.plugins, kindFilter, showInstalled))}</div>
    ${raw(detailsCard(
      "Plugins (plugins/)",
      `<div class="local-plugin-list">${localRows.join("") || '<p class="empty-state">No plugin in this project.</p>'}</div>`,
      { open: local.files.length > 0 },
    ))}
    <div class="card">
      <h2>Install plugin</h2>
      <p class="subtitle">Payload ships no reader/writer of its own — install one from a local .py file, a raw .py URL, or drag one in. See the <a href="#/docs/plugins">plugin guide</a> for ready-to-use ones in examples/plugins/.</p>
      <label class="import-drop" id="plugin-install-drop" for="plugin-install-file-input">
        ${icon("download")}
        <span>Drag a .py plugin file here, or click to choose</span>
      </label>
      <input type="file" id="plugin-install-file-input" accept=".py" multiple hidden>
      <div class="field-row mt-14">
        <div class="field"><label>Local path or http(s):// URL</label><input type="text" id="pi-source" placeholder="examples/plugins/raw_text.py"></div>
        <div class="field"><label>Install as (optional)</label><input type="text" id="pi-as-name" placeholder="my_reader.py"></div>
      </div>
      <button class="primary" id="pi-install">${icon("download")}Install</button>
    </div>
    <div class="card">
      <h2>New plugin</h2>
      <div class="field-row">
        <div class="field"><label>Name</label><input type="text" id="pn-name" placeholder="my_format"></div>
        <div class="field"><label>Kind</label>
          <select id="pn-kind"><option value="reader">reader</option><option value="writer">writer</option><option value="doctor-check">doctor-check</option></select>
        </div>
      </div>
      <button class="primary" id="pn-create">${icon("plus")}Create and open in editor</button>
    </div>
  `;

  const pluginDropZone = document.getElementById("plugin-install-drop");
  const pluginInstallFileInput = document.getElementById("plugin-install-file-input");
  ["dragenter", "dragover"].forEach((evt) => pluginDropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    pluginDropZone.classList.add("import-drop-active");
  }));
  ["dragleave", "drop"].forEach((evt) => pluginDropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    pluginDropZone.classList.remove("import-drop-active");
  }));
  pluginDropZone.addEventListener("drop", (ev) => _handlePluginInstallFiles(ev.dataTransfer.files));
  pluginInstallFileInput.addEventListener("change", () => {
    _handlePluginInstallFiles(pluginInstallFileInput.files);
    pluginInstallFileInput.value = "";
  });

  document.getElementById("pi-install").onclick = async () => {
    const source = val("pi-source");
    if (!source) { toast("Enter a local path or URL first", "warn"); return; }
    const asName = val("pi-as-name");
    const formData = new FormData();
    formData.append("source", source);
    if (asName) formData.append("as_name", asName);
    try {
      let r;
      try {
        r = await apiUpload("/api/plugin/install", formData);
      } catch (e) {
        if (e.data && e.data.error === "PluginAlreadyExistsError") {
          const ok = await confirmDialog(`${e.message}. Overwrite it?`, { danger: true, confirmLabel: "Overwrite" });
          if (!ok) return;
          formData.append("overwrite", "true");
          r = await apiUpload("/api/plugin/install", formData);
        } else {
          throw e;
        }
      }
      invalidatePluginsCache();
      if (r.sanity_ok) {
        toast(`'${r.filename}' installed (${r.kinds.join(", ")})`, "ok");
      } else {
        toast(`'${r.filename}' installed, but doesn't look like a valid plugin: ${r.sanity_issues.join("; ")}`, "warn");
      }
      viewPlugins();
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("pn-create").onclick = async () => {
    try {
      const r2 = await api("/api/plugin/new-local", { body: { name: val("pn-name"), kind: document.getElementById("pn-kind").value } });
      const createdFilename = r2.created.split(/[\\/]/).pop();
      invalidatePluginsCache();
      toast(`Created ${r2.created}`, "ok");
      location.hash = "#/local-plugin/" + encodeURIComponent(createdFilename);
    } catch (e) {
      toastError(e);
    }
  };

  document.querySelectorAll("[data-del-local-plugin]").forEach((btn) => {
    btn.onclick = () => deleteLocalPlugin(btn.dataset.delLocalPlugin, viewPlugins);
  });

  const rerenderPluginGrid = () => {
    document.getElementById("plugin-grid-wrap").innerHTML = _pluginGridHtml(r.plugins, kindFilter, showInstalled);
  };

  document.querySelectorAll("#plugin-kind-filters [data-kind-filter]").forEach((btn) => {
    btn.onclick = () => {
      kindFilter = btn.dataset.kindFilter;
      document.querySelectorAll("#plugin-kind-filters [data-kind-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      rerenderPluginGrid();
    };
  });

  const showInstalledCheckbox = document.getElementById("plugin-show-installed");
  if (showInstalledCheckbox) {
    showInstalledCheckbox.checked = showInstalled;
    showInstalledCheckbox.onchange = (e) => {
      showInstalled = e.target.checked;
      localStorage.setItem("showInstalledPlugins", showInstalled);
      rerenderPluginGrid();
    };
  }
}


/* Installs one already-selected/dropped .py file — on a name
 * collision, asks to overwrite instead of silently refusing (same
 * pattern as _handleImportFiles for tables), then retries once with
 * 'overwrite'. Throws if the user declines the overwrite. */
async function _installOnePluginFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  try {
    return await apiUpload("/api/plugin/install", formData);
  } catch (e) {
    if (e.data && e.data.error === "PluginAlreadyExistsError") {
      const ok = await confirmDialog(`${e.message}. Overwrite it?`, { danger: true, confirmLabel: "Overwrite" });
      if (!ok) throw e;
      formData.append("overwrite", "true");
      return await apiUpload("/api/plugin/install", formData);
    }
    throw e;
  }
}

/* Drag&drop / file-picker install: N .py files dropped together are
 * installed one by one (a collision on one doesn't stop the others —
 * same 'partial but explicit' spirit as bulk table import, just
 * without a summary dialog since a failure is already reported as it
 * happens via toast, and plugin drops are a handful of files, not
 * hundreds). */
async function _handlePluginInstallFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;
  const pyFiles = files.filter((f) => f.name.endsWith(".py"));
  const skipped = files.filter((f) => !f.name.endsWith(".py"));
  if (skipped.length) {
    toast(`Only .py files can be installed as a plugin (ignored: ${skipped.map((f) => f.name).join(", ")})`, "warn");
  }

  let installed = 0;
  for (const f of pyFiles) {
    try {
      const r = await _installOnePluginFile(f);
      installed++;
      if (!r.sanity_ok) {
        toast(`'${r.filename}' installed, but doesn't look like a valid plugin: ${r.sanity_issues.join("; ")}`, "warn");
      }
    } catch (e) {
      toastError(e);
    }
  }
  if (installed) {
    invalidatePluginsCache();
    toast(`${installed} plugin${installed === 1 ? "" : "s"} installed`, "ok");
    viewPlugins();
  }
}

async function deleteLocalPlugin(filename, onDeleted) {
  const ok = await confirmDialog(`Permanently delete '${filename}'? This action can't be undone.`, { danger: true, confirmLabel: "Delete" });
  if (!ok) return;
  try {
    await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "DELETE" });
    invalidatePluginsCache();
    toast(`'${filename}' deleted`, "ok");
    onDeleted();
  } catch (e) {
    toastError(e);
  }
}

async function viewPluginDetail(name) {
  const p = await api("/api/plugin/" + encodeURIComponent(name));

  const chips = [
    p.extensions ? metaChip("Extensions", p.extensions.join(", ")) : "",
    p.extension ? metaChip("Output extension", p.extension) : "",
    p.default_writer ? metaChip("Suggested writer", p.default_writer) : "",
    p.compatible_readers ? metaChip("Only compatible with", p.compatible_readers.join(", ")) : "",
  ].filter(Boolean).join("");

  document.getElementById("content").innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugins</a></div>
    ${raw(pageHeader(p.name, `${p.kind} · API v${p.api_version}${p.installed ? " · installed (pip)" : ""}`))}
    <div class="card">
      ${raw(chips ? `<div class="plugin-meta-row">${chips}</div>` : "")}
      <div class="plugin-description">${raw(formatDescription(p.docstring))}</div>
    </div>
    <div class="card">
      <h2>Validate conformance</h2>
      <div class="field"><label>Sample file (reader only)</label><input type="text" id="pv-sample" placeholder="example.raw"></div>
      <button id="pv-run">Validate</button>
      <div id="pv-result"></div>
    </div>
  `;
  document.getElementById("pv-run").onclick = async () => {
    const r = await api("/api/plugin/validate", { body: { name, sample: val("pv-sample") || undefined } });
    const el = document.getElementById("pv-result");
    if (r.conforms) {
      el.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforms to the contract${r.skipped_behavior_check ? " (structure only, no sample provided)" : ""}</span></div>`;
    } else {
      const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
      el.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>doesn't conform</span><ul>${items}</ul></div>`;
    }
  };
}

/* ---------- local plugin editor (CodeMirror 5, vendored) ---------- */

/* Lazy loading: codemirror.js + mode/python (~420KB together) have no
 * reason to weigh down EVERY page, only the editor's — loaded the
 * first time they're needed, then the same resolved Promise is reused
 * (no double <script> if the editor is reopened). */
let _cmLoadPromise = null;
function loadCodeMirror() {
  if (_cmLoadPromise) return _cmLoadPromise;
  _cmLoadPromise = new Promise((resolveFn, rejectFn) => {
    if (!document.getElementById("cm-css")) {
      const cssLink = document.createElement("link");
      cssLink.id = "cm-css";
      cssLink.rel = "stylesheet";
      cssLink.href = "/static/vendor/codemirror/codemirror.css";
      document.head.appendChild(cssLink);
    }
    const coreScript = document.createElement("script");
    coreScript.src = "/static/vendor/codemirror/codemirror.js";
    coreScript.onload = () => {
      const modeScript = document.createElement("script");
      modeScript.src = "/static/vendor/codemirror/mode/python/python.js";
      modeScript.onload = () => resolveFn(window.CodeMirror);
      modeScript.onerror = () => rejectFn(new Error("Couldn't load the editor (Python mode)"));
      document.body.appendChild(modeScript);
    };
    coreScript.onerror = () => rejectFn(new Error("Couldn't load the editor (codemirror.js)"));
    document.body.appendChild(coreScript);
  });
  return _cmLoadPromise;
}

function _renderPluginTestResults(r) {
  if (!r.loadable) {
    return render`<div class="result-line">${statusPill("fail")}<span>${r.error}</span></div>`;
  }
  return r.results.map((res) => {
    if (!res.loadable) {
      return render`<div class="result-line">${statusPill("fail")}<span><strong>${res.name}</strong> (${res.kind}): ${res.error}</span></div>`;
    }
    const items = (res.issues || []).map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
    return render`
      <div class="result-line">
        ${statusPill(res.conforms ? "ok" : "fail")}
        <span><strong>${res.name}</strong> (${res.kind})${res.skipped_behavior_check ? " — structure only, no sample provided" : ""}</span>
        ${raw(items.length ? `<ul>${items.join("")}</ul>` : "")}
      </div>
    `;
  }).join("");
}

async function viewLocalPluginEditor(rawFilename) {
  const filename = decodeURIComponent(rawFilename);
  const content = document.getElementById("content");

  const [fileData] = await Promise.all([api("/api/local-plugins/" + encodeURIComponent(filename)), loadCodeMirror()]);

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugins</a></div>
    ${raw(pageHeader(filename, "Plugin editor — edit, check syntax, and test conformance directly from here."))}
    <div class="card">
      <div class="field-row align-center">
        <div class="field flex-2 min-w-220"><label>Sample file for the test (reader only, optional)</label><input type="text" id="lpe-sample" placeholder="example.raw"></div>
        <div class="field flex-none"><label>Syntax</label><span class="pill pill-dim" id="lpe-syntax-status">checking…</span></div>
      </div>
      <textarea id="lpe-editor"></textarea>
      <div class="toolbar mt-12">
        <button class="primary" id="lpe-save">${icon("save")}Save</button>
        <button id="lpe-test">Test plugin</button>
        <button class="danger ml-auto" id="lpe-delete">${icon("trash")}Delete plugin</button>
      </div>
      <div id="lpe-result"></div>
    </div>
  `;

  const textarea = document.getElementById("lpe-editor");
  textarea.value = fileData.content;
  const cm = window.CodeMirror.fromTextArea(textarea, {
    mode: "python",
    theme: "payload",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    viewportMargin: Infinity,
    extraKeys: { Tab: (instance) => instance.replaceSelection("    ") },
  });

  // unsaved-changes guard: dirty until the editor content matches what
  // was loaded (both Save and Test persist it, turning the guard clean)
  let originalContent = fileData.content;
  registerDirtyGuard("local-plugin-editor", {
    message: `'${filename}' has unsaved changes.`,
    isDirty: () => cm.getValue() !== originalContent,
  });

  let errorLine = null;
  const setSyntaxStatus = (html) => {
    const el = document.getElementById("lpe-syntax-status");
    if (el) el.outerHTML = html;
  };
  const runSyntaxCheck = debounce(async () => {
    try {
      const r = await api("/api/local-plugins/syntax-check", { body: { content: cm.getValue() } });
      if (errorLine !== null) { cm.removeLineClass(errorLine, "background", "cm-error-line"); errorLine = null; }
      if (r.valid) {
        setSyntaxStatus(`<span class="pill pill-ok" id="lpe-syntax-status">${iconSpan("check")}valid syntax</span>`);
      } else {
        setSyntaxStatus(`<span class="pill pill-fail" id="lpe-syntax-status">${iconSpan("cross")}line ${r.line || "?"}: ${escapeHtml(r.message || "syntax error")}</span>`);
        if (r.line && r.line - 1 < cm.lineCount()) {
          errorLine = r.line - 1;
          cm.addLineClass(errorLine, "background", "cm-error-line");
        }
      }
    } catch (e) {
      // syntax checking is an extra: a failure here shouldn't interrupt editing
    }
  }, 500);

  cm.on("change", runSyntaxCheck);
  runSyntaxCheck();

  document.getElementById("lpe-save").onclick = async () => {
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
      originalContent = cm.getValue(); // saved: guard turns clean
      invalidatePluginsCache();
      toast("Saved", "ok");
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("lpe-test").onclick = async () => {
    const resultEl = document.getElementById("lpe-result");
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
      originalContent = cm.getValue(); // Test also persists the content
      invalidatePluginsCache();
      const sample = val("lpe-sample");
      const r = await api("/api/local-plugins/" + encodeURIComponent(filename) + "/test", { body: { sample: sample || undefined } });
      resultEl.innerHTML = _renderPluginTestResults(r);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("lpe-delete").onclick = () => deleteLocalPlugin(filename, () => { clearDirtyGuards(); location.hash = "#/plugins"; });
}

export { viewPlugins, viewPluginDetail, viewLocalPluginEditor };
