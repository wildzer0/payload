/* Project file browser (route "/files") — the "never touch the folder"
 * surface: a desktop-style tree with right-click context menus, a text
 * editor and a paged hex view, plus full CRUD / upload / download.
 * All paths are RELATIVE to the served project root; the backend
 * (web/routes/fs.py) enforces containment with resolve_contained(), so
 * a traversal attempt here fails server-side regardless of the client. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, promptDialog, openDialog, openContextMenu, fmtBytes,
  registerDirtyGuard, pageHeader, emptyCard, loadCodeMirror, statusPill,
  openTextEditorModal,
} from "../ui.js";
import { api, apiUpload, invalidateTableSources } from "../api.js";

function _relJoin(dir, name) { return dir ? `${dir}/${name}` : name; }
function _dirOf(rel) { const i = rel.lastIndexOf("/"); return i < 0 ? "" : rel.slice(0, i); }
function _baseOf(rel) { const i = rel.lastIndexOf("/"); return i < 0 ? rel : rel.slice(i + 1); }

async function viewFiles(rawPath) {
  const content = document.getElementById("content");
  const state = {
    showInternal: localStorage.getItem("payload-fs-internal") === "true",
    expanded: new Set([""]),
    entries: new Map(),   // relDir -> entries[] (null while first-loading)
    pending: new Map(),   // relDir -> in-flight loadDir promise
    selectedPath: null,   // primary selection (drives the detail panel)
    selectedPaths: new Set(), // multi-selection (Cmd/Ctrl+click, Shift range)
    lastClicked: null,    // anchor for Shift-range selection
    selectedTable: null,  // table_name of the selected file, if any
  };

  const currentDir = () => (state.selectedPath ? _dirOf(state.selectedPath) : "");

  /* ---------- data ---------- */

  /* One in-flight request per dir: a second expand click on a dir that's
   * still loading awaits the same request instead of collapsing it. */
  const loadDir = async (dir, force) => {
    if (state.pending.has(dir)) return state.pending.get(dir);
    if (!force && state.entries.has(dir)) return;
    // a refresh keeps the previous content visible (no flicker, and no
    // dead "loading…" row that would swallow the first click); the
    // loading placeholder only appears on a dir's very first load
    const firstLoad = !state.entries.has(dir);
    if (firstLoad) state.entries.set(dir, null);
    renderTree();
    const promise = (async () => {
      try {
        const r = await api(`/api/fs/tree?path=${encodeURIComponent(dir)}&show_internal=${state.showInternal}`);
        // keyed by the REQUESTED dir (not the server echo): a symlinked
        // folder would otherwise mismatch and render expanded-but-empty
        state.entries.set(dir, r.entries);
      } catch (e) {
        toastError(e);
        state.entries.delete(dir);
      } finally {
        state.pending.delete(dir);
        renderTree();
      }
    })();
    state.pending.set(dir, promise);
    return promise;
  };

  const refreshDir = (dir) => loadDir(dir, true);

  const findEntry = (rel) => {
    const entries = state.entries.get(_dirOf(rel));
    return entries ? entries.find((e) => e.name === _baseOf(rel)) : null;
  };

  /* ---------- multi-selection ---------- */

  /* All currently visible files in tree order — the order Shift-range
   * selection follows (what the user sees is what gets selected). */
  const fileRels = () => {
    const out = [];
    const walk = (dir) => {
      const entries = state.entries.get(dir);
      if (!entries) return;
      for (const e of entries) {
        const rel = _relJoin(dir, e.name);
        if (e.is_dir) {
          if (state.expanded.has(rel)) walk(rel);
        } else {
          out.push(rel);
        }
      }
    };
    walk("");
    return out;
  };

  const selectedList = () => [...state.selectedPaths];

  const setSelectedRows = () => {
    const el = document.getElementById("fs-tree");
    if (!el) return;
    el.querySelectorAll(".fs-row").forEach((row) => {
      const btn = row.querySelector("[data-fs]");
      if (!btn || btn.dataset.kind === "dir") return;
      row.classList.toggle("fs-selected", state.selectedPaths.has(btn.dataset.fs));
    });
  };

  const clearSelection = () => {
    state.selectedPath = null;
    state.selectedTable = null;
    state.selectedPaths.clear();
    state.lastClicked = null;
    setSelectedRows();
    updateSelectionBar();
  };

  const updateSelectionBar = () => {
    const bar = document.getElementById("fs-selection-bar");
    if (!bar) return;
    const n = state.selectedPaths.size;
    bar.hidden = n < 2;
    if (n >= 2) document.getElementById("fs-selection-count").textContent = `${n} selected`;
  };

  /* ---------- batch operations (multi-selection) ---------- */

  const doBatchMove = async () => {
    const paths = selectedList();
    const dest = await promptDialog(`Move ${paths.length} files to folder (relative to the project root)?`, { placeholder: "e.g. sensors", confirmLabel: "Move" });
    if (!dest) return;
    const destDir = String(dest).trim().replace(/^\/+|\/+$/g, "");
    if (!destDir) { toast("Enter a destination folder", "warn"); return; }
    let ok = 0;
    for (const rel of paths) {
      try {
        await api("/api/fs/rename", { body: { path: rel, new_path: _relJoin(destDir, _baseOf(rel)) } });
        ok++;
      } catch (e) {
        toastError(e);
      }
    }
    await Promise.all([refreshDir(destDir), ...paths.map((p) => refreshDir(_dirOf(p)))]);
    invalidateTableSources();
    clearSelection();
    toast(ok === paths.length ? `Moved ${ok} files` : `Moved ${ok} of ${paths.length} files`, ok ? "ok" : "warn");
  };

  const doBatchCopy = async () => {
    const paths = selectedList();
    const dest = await promptDialog(`Copy ${paths.length} files to folder (relative to the project root)?`, { placeholder: "e.g. sensors", confirmLabel: "Copy" });
    if (!dest) return;
    const destDir = String(dest).trim().replace(/^\/+|\/+$/g, "");
    if (!destDir) { toast("Enter a destination folder", "warn"); return; }
    let ok = 0;
    for (const rel of paths) {
      try {
        await api("/api/fs/copy", { body: { path: rel, new_path: _relJoin(destDir, _baseOf(rel)) } });
        ok++;
      } catch (e) {
        toastError(e);
      }
    }
    refreshDir(destDir);
    invalidateTableSources();
    toast(ok === paths.length ? `Copied ${ok} files` : `Copied ${ok} of ${paths.length} files`, ok ? "ok" : "warn");
  };

  const doBatchDelete = async () => {
    const paths = selectedList();
    const ok = await confirmDialog(`Delete these ${paths.length} files? This can't be undone.`, { danger: true, confirmLabel: "Delete" });
    if (!ok) return;
    for (const rel of paths) {
      try {
        await api("/api/fs/delete", { body: { path: rel, confirm: true } });
      } catch (e) {
        toastError(e);
      }
    }
    const dirs = new Set(paths.map(_dirOf));
    await Promise.all([...dirs].map((d) => refreshDir(d)));
    invalidateTableSources();
    clearSelection();
    document.getElementById("fs-detail").innerHTML = '<p class="empty-state">Select a file on the left, or use the buttons above to create/upload. Right-click for more actions.</p>';
    toast(`Deleted ${paths.length} files`, "ok");
  };

  /* ---------- tree ---------- */

  function renderTree() {
    const el = document.getElementById("fs-tree");
    if (!el) return;
    const rows = [];
    const walk = (dir, depth) => {
      const entries = state.entries.get(dir);
      if (entries === null) {
        rows.push(`<div class="fs-row fs-loading" style="--depth:${depth}"><span class="subtitle">loading…</span></div>`);
        return;
      }
      if (!entries) return;
      for (const e of entries) {
        const rel = _relJoin(dir, e.name);
        const isOpen = e.is_dir && state.expanded.has(rel);
        const badge = e.is_dir ? "" : (
          e.table_name
            ? `<span class="fs-badge${e.is_batch_member ? " fs-badge-batch" : " fs-badge-table"}" title="${e.is_batch_member ? `member of batch table '${e.table_name}'` : `table source '${e.table_name}'`}">${iconSpan("box")}${escapeHtml(e.table_name)}</span>`
            : e.sidecar_table
              ? `<span class="fs-badge fs-badge-sidecar" title="sidecar for table '${e.sidecar_table}'">${iconSpan("edit")}${escapeHtml(e.sidecar_table)}</span>`
              : ""
        );
        rows.push(`
          <div class="fs-row" draggable="true" style="--depth:${depth}">
            <button type="button" class="fs-row-main" data-fs="${escapeHtml(rel)}" data-kind="${e.is_dir ? "dir" : "file"}" title="${escapeHtml(rel)}">
              <span class="fs-chevron${isOpen ? " open" : ""}">${e.is_dir ? iconSpan("down") : ""}</span>
              ${iconSpan(e.is_dir ? "folder" : "file")}
              <span class="fs-name">${escapeHtml(e.name)}</span>
              ${badge}
            </button>
            ${e.is_dir ? "" : `<span class="fs-size mono">${fmtBytes(e.size)}</span>`}
          </div>`);
        if (e.is_dir && isOpen) walk(rel, depth + 1);
      }
    };
    walk("", 0);
    el.innerHTML = rows.join("") || '<p class="empty-state m-0">Empty project folder.</p>';
    setSelectedRows(); // selection is applied centrally, not baked into the HTML
  }

  /* ---------- detail: text editor / hex view ---------- */

  /* Monotone counter so a slow read for a file you've already clicked
   * away from can't clobber the newer detail view. */
  const detailSeq = { n: 0 };

  async function loadDetail(rel, asHex, startOffset) {
    const el = document.getElementById("fs-detail");
    if (!el) return;
    if (!rel || rel === "undefined" || rel === "null") {
      // a call arrived with no real path (stale selection / a stale
      // "#/files/undefined" URL / broken row): never hit the API with
      // "?path=undefined" — show the empty state and invalidate any
      // in-flight load for a stale path
      detailSeq.n++;
      el.innerHTML = '<p class="empty-state">Select a file on the left, or use the buttons above to create/upload. Right-click for more actions.</p>';
      renderLocation();
      return;
    }
    const seq = ++detailSeq.n;
    const entry = findEntry(rel);
    state.selectedTable = entry ? entry.table_name : null;
    state.selectedPath = rel;
    state.selectedPaths = new Set([rel]);
    state.lastClicked = rel;
    setSelectedRows();
    updateSelectionBar();
    // keep the current content (or the empty state on the very first
    // click) visible as-is while the new file loads, then swap in one
    // step — no placeholder and no "Loading…" flash
    if (!el.querySelector(".fs-detail-head")) {
      // first load: nothing to show yet, just leave the empty state
      el.textContent = ""; // no-op fallback; innerHTML stays as-is
    }
    try {
      const params = new URLSearchParams();
      params.set("path", rel);
      if (asHex) params.set("as_hex", "1");
      if (startOffset != null) params.set("offset", String(startOffset));
      const r = await api(`/api/fs/read?${params.toString()}`);
      if (seq !== detailSeq.n) return; // a newer selection superseded this one
      await renderDetail(r, { asHexRequested: !!asHex });
    } catch (e) {
      if (seq !== detailSeq.n) return;
      el.innerHTML = emptyCard(e.message, e.hint);
    }
    renderLocation();
  }

  async function renderDetail(r, opts) {
    opts = opts || {};
    const el = document.getElementById("fs-detail");
    const rel = r.path;
    const dir = _dirOf(rel);
    // two labeled, equal-width groups: Content (what you do with the
    // bytes) and Manage (what you do with the file)
    const contentActions = `
      <button class="primary" id="fs-edit">${iconSpan("edit")}${r.is_text ? "Edit" : "Edit as text"}</button>
      <button id="fs-hex-view">${iconSpan("box")}View hex</button>
      <button id="fs-analyze">${iconSpan("box")}Analyze</button>
      <button id="fs-compare">${iconSpan("edit")}Compare…</button>
    `;
    const manageActions = `
      ${state.selectedTable ? `<a class="btn" href="#/table/${encodeURIComponent(state.selectedTable)}">${iconSpan("box")}Open table</a>` : ""}
      <a class="btn" href="/api/fs/download?path=${encodeURIComponent(rel)}" download>${iconSpan("download")}Download</a>
      <button id="fs-rename">${iconSpan("edit")}Rename</button>
      <button id="fs-copy">${iconSpan("copy")}Copy</button>
      <button id="fs-copy-to">${iconSpan("copy")}Copy to…</button>
      <button id="fs-move">${iconSpan("folder")}Move</button>
      <button class="danger" id="fs-delete">${iconSpan("trash")}Delete</button>
    `;
    const actionsHtml = `
      <div class="fs-action-group">
        <span class="fs-action-label">Content</span>
        ${contentActions}
      </div>
      <div class="fs-action-group">
        <span class="fs-action-label">Manage</span>
        ${manageActions}
      </div>
    `;
    const metaHtml = `
      <span class="meta-chip"><strong>Path</strong><span class="mono">${rel}</span></span>
      <span class="meta-chip"><strong>Size</strong><span class="mono">${fmtBytes(r.size)}</span></span>
    `;

    const editTitle = _baseOf(rel);
    const head = render`
      <div class="fs-detail-head">
        <div>
          <h2 class="m-0">${editTitle}</h2>
          <div class="fs-detail-meta">${raw(metaHtml)}</div>
        </div>
        <div class="fs-detail-actions">${raw(actionsHtml)}</div>
      </div>
    `;

    // content actions (row): same for text and binary, so the context
    // menu can reuse them uniformly
    const openEditorModal = (initialContent, truncated, onSave) => openTextEditorModal({
      title: editTitle,
      subtitle: rel,
      initialContent,
      readOnly: !!truncated,
      guardId: "fs-editor",
      onSave,
    });

    el.innerHTML = render`
      ${raw(head)}
      ${raw(r.truncated
        ? `<div class="result-line">${iconSpan("warnTri")}<span class="subtitle">File too large (${fmtBytes(r.size)}) — the editor opens read-only (first ${fmtBytes(r.content.length)} shown).</span></div>`
        : "")}
      <div id="fs-analyze-panel" class="analyze-panel" hidden></div>
      <div id="fs-compare-panel" class="compare-panel" hidden></div>
    `;

    wireDetailActions(rel);
    wireInspectActions(rel);

    const editBtn = document.getElementById("fs-edit");
    if (editBtn) {
      editBtn.onclick = () => {
        if (r.is_text) {
          openEditorModal(r.content, r.truncated, async (content) => {
            await api("/api/fs/write", { method: "PUT", body: { path: rel, content } });
            refreshDir(dir);
          });
        } else {
          // binary "Edit as text": the hex read has no text — re-read
          api(`/api/fs/read?path=${encodeURIComponent(rel)}`).then((t) => {
            if (!t.is_text) { toast("This file isn't UTF-8 text", "warn"); return; }
            openEditorModal(t.content, t.truncated, async (content) => {
              await api("/api/fs/write", { method: "PUT", body: { path: rel, content } });
              refreshDir(dir);
            });
          }).catch(toastError);
        }
      };
    }
    const hexViewBtn = document.getElementById("fs-hex-view");
    if (hexViewBtn) hexViewBtn.onclick = () => openHexModal(rel, r);
  }

  /* Hex content in a near-fullscreen modal (the old inline paged hex
   * table + strings toggle, moved out of the detail pane). A text file
   * shown as text has no rows: it's re-read with as_hex=1. */
  async function openHexModal(rel, r) {
    openDialog({
      large: true,
      title: _baseOf(rel),
      body: render`<p class="empty-state m-0">Loading…</p>`,
      actions: [{ label: "Close" }],
    });
    try {
      const hex = (r && Array.isArray(r.rows)) ? r : await api(`/api/fs/read?path=${encodeURIComponent(rel)}&as_hex=1`);
      const overlay = document.getElementById("modal-overlay");
      if (overlay.hidden) return; // closed while loading
      const box = document.getElementById("modal-box");
      box.innerHTML = render`
        <div class="fs-binary-toolbar">
          <span class="subtitle mono">${fmtBytes(hex.size)}</span>
          <span class="flex-1"></span>
          <label class="toggle-chip"><input type="checkbox" id="fs-strings-toggle"><span>Strings</span></label>
          <span class="fs-pager">
            <button id="fs-page-prev" ${hex.offset === 0 ? "disabled" : ""} title="Previous page">${icon("up")}</button>
            <span class="mono fs-offset-label" id="fs-offset-label" title="Visible byte range">${raw(offsetRangeLabel(hex))}</span>
            <button id="fs-page-next" ${hex.has_more ? "" : "disabled"} title="Next page">${icon("down")}</button>
          </span>
        </div>
        <div class="fs-hex" id="fs-hex">${raw(hexRowsHtml(hex))}</div>
        <div id="fs-strings" hidden></div>
      `;
      wireBinaryView(rel, hex);
    } catch (e) {
      const box = document.getElementById("modal-box");
      box.innerHTML = emptyCard(e.message, e.hint);
    }
  }

  function hexRowsHtml(r) {
    const rows = r.rows.map((row) => {
      const bytes = row.hex.split(" ");
      const g1 = bytes.slice(0, 8).join(" ");
      const g2 = bytes.slice(8).join(" ");
      return `
        <tr>
          <td class="hex-offset">0x${row.offset.toString(16).padStart(4, "0").toUpperCase()}</td>
          <td class="hex-bytes"><span class="hex-group">${escapeHtml(g1)}</span><span class="hex-group">${escapeHtml(g2)}</span></td>
          <td class="hex-ascii">${escapeHtml(row.ascii)}</td>
        </tr>`;
    }).join("");
    // a real table: column alignment is guaranteed by the table layout
    // (no min-width tricks), and the header row makes it read as a hex
    // editor instead of stray divs
    return `
      <div class="hex-table-wrap">
        <table class="hex-table">
          <thead><tr>
            <th class="hex-offset">Offset</th>
            <th class="hex-bytes-head">00 01 02 03 04 05 06 07 &nbsp;&nbsp; 08 09 0A 0B 0C 0D 0E 0F</th>
            <th class="hex-ascii">ASCII</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function offsetRangeLabel(r) {
    const hex = (n) => "0x" + n.toString(16).padStart(4, "0").toUpperCase();
    const bytes = (r.rows || []).reduce((n, row) => n + row.hex.split(" ").length, 0);
    return `${hex(r.offset)}–${hex(r.offset + bytes)}`;
  }

  /* File picker for "Compare with…": a searchable list of the project's
   * files (see /api/fs/list), palette-styled. Resolves with the chosen
   * relative path, or null when cancelled. */
  const pickFileDialog = () => new Promise(async (resolveFn) => {
    let files = [];
    let error = null;
    try {
      files = (await api("/api/fs/list")).files || [];
    } catch (e) {
      error = e.message;
    }
    const overlay = document.createElement("div");
    overlay.className = "palette-overlay";
    overlay.innerHTML = `
      <div class="palette pick-file" role="dialog" aria-label="Pick a file">
        <input class="palette-input" placeholder="Choose a file to compare with…">
        <div class="palette-results"></div>
      </div>`;
    document.body.appendChild(overlay);
    const input = overlay.querySelector(".palette-input");
    const results = overlay.querySelector(".palette-results");
    const close = (result) => { overlay.remove(); resolveFn(result); };
    const render = () => {
      results.textContent = "";
      if (error) {
        const empty = document.createElement("div");
        empty.className = "palette-empty";
        empty.textContent = `Couldn't load the file list: ${error}`;
        results.appendChild(empty);
        return;
      }
      if (!files.length) {
        const empty = document.createElement("div");
        empty.className = "palette-empty";
        empty.textContent = "No file in this project.";
        results.appendChild(empty);
        return;
      }
      const q = input.value.trim().toLowerCase();
      const filtered = q ? files.filter((f) => f.toLowerCase().includes(q)) : files;
      if (!filtered.length) {
        const empty = document.createElement("div");
        empty.className = "palette-empty";
        empty.textContent = "No file matches.";
        results.appendChild(empty);
        return;
      }
      filtered.forEach((rel) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "palette-item";
        const name = document.createElement("span");
        name.className = "palette-item-label";
        name.textContent = _baseOf(rel);
        const hint = document.createElement("span");
        hint.className = "palette-item-hint";
        hint.textContent = _dirOf(rel) || "/";
        row.appendChild(name);
        row.appendChild(hint);
        row.addEventListener("mousedown", (ev) => { ev.preventDefault(); close(rel); });
        results.appendChild(row);
      });
    };
    input.addEventListener("input", render);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.preventDefault(); close(null); } });
    overlay.addEventListener("mousedown", (ev) => { if (ev.target === overlay) close(null); });
    input.focus();
    render();
  });

  /* Analyze (entropy/magic/freq) and Compare (byte diff vs another
   * file) for the file being inspected — results render in the light
   * detail panel (not a content preview). */
  async function wireInspectActions(rel) {
    const analyzeBtn = document.getElementById("fs-analyze");
    if (analyzeBtn) {
      analyzeBtn.onclick = async () => {
        const panel = document.getElementById("fs-analyze-panel");
        panel.hidden = false;
        panel.innerHTML = '<p class="empty-state m-0">Analyzing…</p>';
        try {
          const a = await api("/api/fs/analyze?path=" + encodeURIComponent(rel));
          const counts = new Map(a.freq);
          const top = [...counts.entries()].sort((x, y) => y[1] - x[1]).slice(0, 32);
          const max = top.length ? top[0][1] : 1;
          panel.innerHTML = render`
            <div class="analyze-grid">
              <div class="analyze-stat"><div class="v">${fmtBytes(a.size)}</div><div class="k">size</div></div>
              <div class="analyze-stat"><div class="v">${a.entropy}</div><div class="k">entropy (bits/byte)</div></div>
              <div class="analyze-stat"><div class="v">${a.distinct}/256</div><div class="k">distinct bytes</div></div>
              <div class="analyze-stat"><div class="v">${Math.round(a.printable_ratio * 100)}%</div><div class="k">printable</div></div>
              <div class="analyze-stat"><div class="v">${Math.round(a.null_ratio * 100)}%</div><div class="k">zero bytes</div></div>
              <div class="analyze-stat"><div class="v">${a.ascii_runs}</div><div class="k">ASCII runs ≥4</div></div>
              <div class="analyze-stat"><div class="v">${a.magic.length ? escapeHtml(a.magic.join(", ")) : "—"}</div><div class="k">magic</div></div>
            </div>
            <div class="analyze-hist-title">Byte frequency — top ${top.length} of 256 values</div>
            <div class="analyze-hist">${top.map(([b, c]) => `<div class="bar" title="0x${b.toString(16).padStart(2, "0").toUpperCase()} ×${c}" style="height:${Math.max(4, Math.round((c / max) * 60))}px"></div>`)}</div>
          `;
        } catch (e) {
          panel.innerHTML = emptyCard(e.message, e.hint);
        }
      };
    }

    const compareBtn = document.getElementById("fs-compare");
    if (compareBtn) {
      compareBtn.onclick = async () => {
        const pathB = await pickFileDialog();
        if (!pathB) return;
        const panel = document.getElementById("fs-compare-panel");
        panel.hidden = false;
        panel.innerHTML = '<p class="empty-state m-0">Comparing…</p>';
        try {
          const c = await api(`/api/fs/compare?path_a=${encodeURIComponent(rel)}&path_b=${encodeURIComponent(pathB)}`);
          if (c.equal) {
            panel.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>Identical (${c.a_size} bytes)</span></div>`;
            return;
          }
          const rows = c.runs.map((run) => `
            <div class="compare-row">
              <span class="mono off">0x${run.offset.toString(16).padStart(4, "0").toUpperCase()}</span>
              <span class="mono">${run.length} byte(s) differ${run.file ? ` — '${escapeHtml(run.file)}' is longer` : ""}</span>
            </div>`).join("");
          panel.innerHTML = render`
            <div class="result-line">${statusPill("mismatch")}<span>${c.a} vs ${c.b} — ${c.a_size} vs ${c.b_size} bytes, ${c.runs.length} differing run(s)</span></div>
            ${raw(rows)}
          `;
        } catch (e) {
          panel.innerHTML = emptyCard(e.message, e.hint);
        }
      };
    }
  }

  function wireBinaryView(rel, r) {
    const pageSize = r.limit || 256;
    const loadPage = async (offset) => {
      try {
        const next = await api(`/api/fs/read?path=${encodeURIComponent(rel)}&offset=${offset}&limit=${pageSize}`);
        const hex = document.getElementById("fs-hex");
        if (hex) hex.innerHTML = hexRowsHtml(next);
        const label = document.getElementById("fs-offset-label");
        if (label) label.textContent = offsetRangeLabel(next);
        const prev = document.getElementById("fs-page-prev");
        const nextBtn = document.getElementById("fs-page-next");
        if (prev) prev.disabled = next.offset === 0;
        if (nextBtn) nextBtn.disabled = !next.has_more;
        return next;
      } catch (e) {
        toastError(e);
        return null;
      }
    };
    const prevBtn = document.getElementById("fs-page-prev");
    const nextBtn = document.getElementById("fs-page-next");
    if (prevBtn) prevBtn.onclick = () => loadPage(Math.max(0, r.offset - pageSize));
    if (nextBtn) nextBtn.onclick = () => loadPage(r.offset + pageSize);
    const toggle = document.getElementById("fs-strings-toggle");
    if (toggle) {
      toggle.onchange = async () => {
        const box = document.getElementById("fs-strings");
        if (!box) return;
        if (!toggle.checked) { box.hidden = true; return; }
        try {
          const s = await api("/api/fs/strings?path=" + encodeURIComponent(rel));
          box.hidden = false;
          box.innerHTML = s.strings.length
            ? s.strings.map((str) => `
                <button type="button" class="fs-string-item" data-offset="${str.offset}">
                  <span class="mono fs-string-offset">0x${str.offset.toString(16).toUpperCase()}</span>
                  <span class="fs-string-text mono">${escapeHtml(str.text)}</span>
                </button>`).join("")
            : '<p class="empty-state m-0">No ASCII strings found in this file.</p>';
          box.querySelectorAll("[data-offset]").forEach((btn) => {
            btn.onclick = () => {
              toggle.checked = false;
              box.hidden = true;
              const aligned = Math.max(0, Math.floor(Number(btn.dataset.offset) / pageSize) * pageSize);
              loadPage(aligned);
            };
          });
        } catch (e) {
          toastError(e);
          box.hidden = true;
        }
      };
    }
  }

  function wireDetailActions(rel) {
    const dir = _dirOf(rel);
    const fileBtn = (id) => document.getElementById(id);
    if (fileBtn("fs-rename")) {
      fileBtn("fs-rename").onclick = () => doRename(rel);
    }
    if (fileBtn("fs-copy")) {
      fileBtn("fs-copy").onclick = () => doCopy(rel);
    }
    if (fileBtn("fs-copy-to")) {
      fileBtn("fs-copy-to").onclick = () => doCopyTo(rel);
    }
    if (fileBtn("fs-move")) {
      fileBtn("fs-move").onclick = () => doMove(rel);
    }
    if (fileBtn("fs-delete")) {
      fileBtn("fs-delete").onclick = () => doDelete(rel);
    }
  }

  /* ---------- mutations ---------- */

  async function doRename(rel) {
    const base = _baseOf(rel);
    const name = await promptDialog(`Rename '${base}' to:`, { value: base, confirmLabel: "Rename" });
    if (!name || name === base) return;
    try {
      const r = await api("/api/fs/rename", { body: { path: rel, new_path: _relJoin(_dirOf(rel), name) } });
      invalidateTableSources(); // a renamed/moved file may be a table source
      toast(`Renamed to '${r.path}'`, "ok");
      if (state.selectedPath === rel) state.selectedPath = r.path;
      await Promise.all([refreshDir(_dirOf(rel)), refreshDir(_dirOf(r.path))]);
      if (state.selectedPath === r.path) loadDetail(r.path);
    } catch (e) {
      toastError(e);
    }
  }

  async function doMove(rel) {
    const base = _baseOf(rel);
    const dest = await promptDialog(`Move '${base}' to folder (relative to the project root)?`, { placeholder: "e.g. sensors", confirmLabel: "Move" });
    if (!dest) return;
    const destDir = String(dest).trim().replace(/^\/+|\/+$/g, "");
    if (!destDir) { toast("Enter a destination folder", "warn"); return; }
    try {
      const r = await api("/api/fs/rename", { body: { path: rel, new_path: _relJoin(destDir, base) } });
      invalidateTableSources();
      toast(`Moved to '${r.path}'`, "ok");
      if (state.selectedPath === rel) state.selectedPath = r.path;
      await Promise.all([refreshDir(_dirOf(rel)), refreshDir(destDir)]);
      if (state.selectedPath === r.path) loadDetail(r.path);
    } catch (e) {
      toastError(e);
    }
  }

  async function doCopyTo(rel) {
    const base = _baseOf(rel);
    const dest = await promptDialog(`Copy '${base}' to folder (relative to the project root)?`, { placeholder: "e.g. sensors", confirmLabel: "Copy" });
    if (!dest) return;
    const destDir = String(dest).trim().replace(/^\/+|\/+$/g, "");
    if (!destDir) { toast("Enter a destination folder", "warn"); return; }
    try {
      const r = await api("/api/fs/copy", { body: { path: rel, new_path: _relJoin(destDir, base) } });
      invalidateTableSources();
      toast(`Copied to '${r.path}'`, "ok");
      refreshDir(destDir);
    } catch (e) {
      toastError(e);
    }
  }

  async function doCopy(rel) {
    const base = _baseOf(rel);
    const dot = base.lastIndexOf(".");
    const suggested = dot > 0 ? `${base.slice(0, dot)} (copy)${base.slice(dot)}` : `${base} (copy)`;
    const name = await promptDialog(`Copy '${base}' as:`, { value: suggested, confirmLabel: "Copy" });
    if (!name) return;
    try {
      const r = await api("/api/fs/copy", { body: { path: rel, new_path: _relJoin(_dirOf(rel), name) } });
      invalidateTableSources();
      toast(`Copied to '${r.path}'`, "ok");
      refreshDir(_dirOf(r.path));
    } catch (e) {
      toastError(e);
    }
  }

  async function doDelete(rel) {
    try {
      const preview = await api("/api/fs/delete", { body: { path: rel } });
      const what = preview.is_dir ? "folder" : "file";
      const countNote = preview.is_dir ? ` (${preview.entries} entries)` : "";
      const ok = await confirmDialog(`Delete ${what} '${rel}'${countNote}? This can't be undone.`, { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      await api("/api/fs/delete", { body: { path: rel, confirm: true } });
      invalidateTableSources();
      toast(`'${rel}' deleted`, "ok");
      if (state.selectedPath === rel) {
        state.selectedPath = null;
        state.selectedTable = null;
        state.selectedPaths.delete(rel);
        updateSelectionBar();
        document.getElementById("fs-detail").innerHTML = '<p class="empty-state">Select a file on the left, or use the buttons above to create/upload.</p>';
      }
      refreshDir(_dirOf(rel));
      renderLocation();
    } catch (e) {
      toastError(e);
    }
  }

  async function doCreate(type) {
    const dir = currentDir();
    const label = type === "dir" ? "folder name" : "file name";
    const name = await promptDialog(`New ${type === "dir" ? "folder" : "file"} in '${dir || "/"}'?`, { placeholder: label, confirmLabel: "Create" });
    if (!name) return;
    try {
      const r = await api("/api/fs/create", { body: { path: _relJoin(dir, name), type } });
      invalidateTableSources();
      toast(`Created '${r.path}'`, "ok");
      refreshDir(dir);
      if (type === "file") loadDetail(r.path);
    } catch (e) {
      toastError(e);
    }
  }

  async function doUpload(files, dir) {
    const list = Array.from(files);
    if (!list.length) return;
    const formData = new FormData();
    formData.append("dir", dir || ".");
    list.forEach((f) => formData.append("file", f));
    try {
      const r = await apiUpload("/api/fs/upload", formData);
      if (r.imported.length) toast(`${r.imported.length} file${r.imported.length === 1 ? "" : "s"} uploaded to '${dir || "/"}'`, "ok");
      if (r.skipped.length) toast(`${r.skipped.length} skipped: ${r.skipped.map((s) => s.name).join(", ")}`, "warn");
      invalidateTableSources();
      refreshDir(dir || "");
    } catch (e) {
      toastError(e);
    }
  }

  /* ---------- location line ---------- */

  function renderLocation() {
    const el = document.getElementById("fs-location");
    if (!el) return;
    el.textContent = state.selectedPath ? `Selected: ${state.selectedPath}` : "Location: /";
  }

  /* ---------- context menus (right-click) ---------- */

  function fileMenu(rel) {
    const entry = findEntry(rel);
    const table = entry ? entry.table_name : null;
    const multi = state.selectedPaths.size > 1 && state.selectedPaths.has(rel);
    const suffix = multi ? ` (${state.selectedPaths.size} files)` : "";
    // right-click must offer EVERYTHING the detail buttons do: select
    // the file first (renders the light detail), then click the same
    // wired button — one source of truth for the handlers
    const clickDetailAction = (id) => async () => {
      await loadDetail(rel);
      const btn = document.getElementById(id);
      if (btn) btn.click();
    };
    const items = [
      { label: "Open", icon: "edit", action: () => loadDetail(rel) },
    ];
    if (!multi) {
      items.push(
        { label: "Edit", icon: "edit", action: clickDetailAction("fs-edit") },
        { label: "View hex", icon: "box", action: clickDetailAction("fs-hex-view") },
        { label: "Analyze", icon: "box", action: clickDetailAction("fs-analyze") },
        { label: "Compare…", icon: "edit", action: clickDetailAction("fs-compare") },
      );
    }
    if (table && !multi) {
      items.push({ label: `Open table '${table}'`, icon: "box", action: () => { location.hash = "#/table/" + encodeURIComponent(table); } });
    }
    items.push(
      { label: "Download", icon: "download", action: () => { window.open("/api/fs/download?path=" + encodeURIComponent(rel), "_blank"); } },
    );
    if (!multi) {
      items.push(
        { label: "Rename", icon: "edit", action: () => doRename(rel) },
        { label: "Copy", icon: "copy", action: () => doCopy(rel) },
      );
    }
    items.push(
      { label: `Copy to…${suffix}`, icon: "copy", action: () => (multi ? doBatchCopy() : doCopyTo(rel)) },
      { label: `Move to…${suffix}`, icon: "folder", action: () => (multi ? doBatchMove() : doMove(rel)) },
      { label: `Delete${suffix}`, icon: "trash", danger: true, action: () => (multi ? doBatchDelete() : doDelete(rel)) },
    );
    return items;
  }

  function dirMenu(rel, isOpen) {
    return [
      { label: isOpen ? "Collapse" : "Expand", icon: "down", action: () => toggleDir(rel) },
      { label: "New file here", icon: "plus", action: () => { state.selectedPath = null; renderTree(); createIn(rel, "file"); } },
      { label: "New folder here", icon: "folder", action: () => { state.selectedPath = null; renderTree(); createIn(rel, "dir"); } },
      { label: "Upload here", icon: "upload", action: () => { state.selectedPath = null; renderTree(); openUploadFor(rel); } },
      { label: "Rename", icon: "edit", action: () => doRename(rel) },
      { label: "Copy", icon: "copy", action: () => doCopy(rel) },
      { label: "Copy to…", icon: "copy", action: () => doCopyTo(rel) },
      { label: "Move to…", icon: "folder", action: () => doMove(rel) },
      { label: "Delete", icon: "trash", danger: true, action: () => doDelete(rel) },
    ];
  }

  function emptyMenu() {
    const dir = currentDir();
    return [
      { label: "New file", icon: "plus", action: () => doCreate("file") },
      { label: "New folder", icon: "folder", action: () => doCreate("dir") },
      { label: "Upload", icon: "upload", action: () => openUploadFor(dir) },
      { label: "Refresh", icon: "refresh", action: () => { refreshDir(dir); refreshDir(""); } },
    ];
  }

  /* ---------- events ---------- */

  const toggleDir = async (rel) => {
    if (state.expanded.has(rel)) {
      // never collapse a dir that is still loading (a double-click while
      // the request is in flight would otherwise toggle it away)
      if (!state.pending.has(rel)) {
        state.expanded.delete(rel);
        renderTree();
      }
      return;
    }
    state.expanded.add(rel);
    // render right away: a cached dir shows its children instantly, a
    // never-loaded one shows the loading placeholder — loadDir must NOT
    // be the only renderer, its early return on a cache hit would leave
    // the folder visually collapsed ("open does nothing")
    renderTree();
    await loadDir(rel);
  };

  const createIn = (dir, type) => {
    // create a file/folder inside 'dir' without selecting anything first
    const name = promptDialog(`New ${type === "dir" ? "folder" : "file"} in '${dir || "/"}'?`, { confirmLabel: "Create" });
    return name.then(async (resolved) => {
      if (!resolved) return;
      try {
        const r = await api("/api/fs/create", { body: { path: _relJoin(dir, resolved), type } });
        toast(`Created '${r.path}'`, "ok");
        refreshDir(dir);
        if (type === "file") loadDetail(r.path);
      } catch (e) {
        toastError(e);
      }
    });
  };

  const openUploadFor = (dir) => {
    const input = document.getElementById("fs-upload-input");
    input.dataset.fsUploadDir = dir || ".";
    input.value = "";
    input.click();
  };

  /* ---------- layout ---------- */

  content.innerHTML = render`
    ${raw(pageHeader("Files", "Browse and manage the whole project folder from here — no need to touch the filesystem."))}
    ${raw(localStorage.getItem("payload-fs-warning-dismissed") === "true" ? "" : `
      <div class="card fs-warning">
        ${iconSpan("warnTri")}
        <div>
          <strong>Advanced area</strong>
          <p>Operations here touch the real files on disk (rename, move, copy, delete, edit) and are meant for users who know what they're doing — there is no undo, and they can affect table data. Use the Dashboard for normal table work.</p>
        </div>
        <button type="button" class="ghost fs-warning-dismiss" id="fs-warning-dismiss" title="Don't show again">${iconSpan("close")}</button>
      </div>`)}
    <div class="card fs-toolbar">
      <div class="fs-toolbar-row">
        <button id="fs-new-file">${icon("plus")}New file</button>
        <button id="fs-new-dir">${icon("folder")}New folder</button>
        <button id="fs-upload-btn">${icon("upload")}Upload</button>
        <span class="flex-1"></span>
        <button id="fs-refresh">${icon("refresh")}Refresh</button>
        <label class="toggle-chip"><input type="checkbox" id="fs-internal" ${state.showInternal ? "checked" : ""}><span>Show internal folders</span></label>
      </div>
      <div class="fs-toolbar-row">
        <input type="text" id="fs-search-input" class="fs-search-input" placeholder="Search file content in the project…">
        <button id="fs-search-btn">${icon("box")}Search</button>
      </div>
      <div class="fs-search-results" id="fs-search-results" hidden></div>
      <div class="fs-selection-bar" id="fs-selection-bar" hidden>
        <span class="fs-selection-count" id="fs-selection-count"></span>
        <span class="flex-1"></span>
        <button id="fs-sel-move">${icon("folder")}Move to…</button>
        <button id="fs-sel-copy">${icon("copy")}Copy to…</button>
        <button class="danger" id="fs-sel-delete">${icon("trash")}Delete</button>
      </div>
      <p class="subtitle m-0" id="fs-location">Location: /</p>
      <input type="file" id="fs-upload-input" multiple hidden>
    </div>
    <div class="fs-layout">
      <div class="card fs-tree-wrap">
        <h2 class="fs-tree-title">Project</h2>
        <div class="fs-tree" id="fs-tree"></div>
      </div>
      <div class="card fs-detail" id="fs-detail">
        <p class="empty-state">Select a file on the left, or use the buttons above to create/upload. Right-click for more actions.</p>
      </div>
    </div>
  `;

  renderLocation();
  await loadDir("");

  const dismissWarning = document.getElementById("fs-warning-dismiss");
  if (dismissWarning) {
    dismissWarning.onclick = () => {
      localStorage.setItem("payload-fs-warning-dismissed", "true");
      const card = dismissWarning.closest(".fs-warning");
      if (card) card.remove();
    };
  }

  // tree: click selects/expands (Cmd/Ctrl toggles, Shift selects a
  // range), right-click opens the context menu
  const treeEl = document.getElementById("fs-tree");
  treeEl.onclick = async (ev) => {
    const btn = ev.target.closest("[data-fs]");
    if (!btn) {
      // clicking the empty tree area (outside any file/folder) clears
      // the multi-selection
      clearSelection();
      return;
    }
    if (btn.dataset.kind === "dir") { toggleDir(btn.dataset.fs); return; }
    const rel = btn.dataset.fs;
    if (ev.metaKey || ev.ctrlKey) {
      if (state.selectedPaths.has(rel)) state.selectedPaths.delete(rel);
      else state.selectedPaths.add(rel);
      state.lastClicked = rel;
      setSelectedRows();
      updateSelectionBar();
      return;
    }
    if (ev.shiftKey && state.lastClicked) {
      const list = fileRels();
      const a = list.indexOf(state.lastClicked);
      const b = list.indexOf(rel);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        state.selectedPaths = new Set(list.slice(lo, hi + 1));
      }
      state.lastClicked = rel;
      setSelectedRows();
      updateSelectionBar();
      return;
    }
    loadDetail(rel);
  };
  treeEl.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    const btn = ev.target.closest("[data-fs]");
    if (btn && btn.dataset.kind === "file") {
      state.selectedPath = btn.dataset.fs;
      openContextMenu(fileMenu(btn.dataset.fs), ev.clientX, ev.clientY);
    } else if (btn && btn.dataset.kind === "dir") {
      openContextMenu(dirMenu(btn.dataset.fs, state.expanded.has(btn.dataset.fs)), ev.clientX, ev.clientY);
    } else {
      openContextMenu(emptyMenu(), ev.clientX, ev.clientY);
    }
  });

  /* ---------- drag & drop (desktop-style) ---------- */

  const DND_TYPE = "application/x-payload-fs";
  const isCopyDrop = (ev) => !!(ev.altKey || ev.shiftKey || ev.ctrlKey || ev.metaKey);

  const clearDropHighlights = () => {
    treeEl.querySelectorAll(".fs-drop-target").forEach((el) => el.classList.remove("fs-drop-target"));
  };
  /* What a drop at this point targets: a dir row -> into that folder,
   * a file row -> next to it (its parent), empty space -> the
   * contextual current dir. */
  const dropTargetInfo = (ev) => {
    const row = ev.target.closest(".fs-row");
    const btn = row && row.querySelector("[data-fs]");
    if (btn && btn.dataset.kind === "dir") return { dir: btn.dataset.fs, el: row };
    if (btn) return { dir: _dirOf(btn.dataset.fs), el: row };
    return { dir: currentDir(), el: treeEl };
  };

  const doDndMoveCopy = async (rel, targetDir, asCopy) => {
    if (!targetDir) { toast("Drop onto a folder to move/copy it", "warn"); return; }
    const base = _baseOf(rel);
    try {
      if (asCopy) {
        const r = await api("/api/fs/copy", { body: { path: rel, new_path: _relJoin(targetDir, base) } });
        toast(`Copied to '${r.path}'`, "ok");
        refreshDir(targetDir);
      } else {
        const r = await api("/api/fs/rename", { body: { path: rel, new_path: _relJoin(targetDir, base) } });
        toast(`Moved to '${r.path}'`, "ok");
        if (state.selectedPath === rel) state.selectedPath = r.path;
        await Promise.all([refreshDir(_dirOf(rel)), refreshDir(targetDir)]);
        if (state.selectedPath === r.path) loadDetail(r.path);
      }
      // dragging an item completes the action: no lingering selection —
      // otherwise every row that was multi-selected before the drag keeps
      // its blue .fs-selected highlight after the tree re-renders
      state.selectedPaths.clear();
      state.lastClicked = null;
      setSelectedRows();
      updateSelectionBar();
    } catch (e) {
      toastError(e);
    }
  };

  treeEl.addEventListener("dragstart", (ev) => {
    const row = ev.target.closest(".fs-row");
    // the drag can start anywhere on the row (it owns draggable=true),
    // not necessarily on the [data-fs] button — read the path from the row
    const btn = row && row.querySelector("[data-fs]");
    if (!row || !btn) return;
    try {
      ev.dataTransfer.setData(DND_TYPE, btn.dataset.fs);
      ev.dataTransfer.effectAllowed = "move";
    } catch (e) { /* some environments restrict dataTransfer */ }
    row.classList.add("fs-dragging");
  });
  treeEl.addEventListener("dragend", () => {
    clearDropHighlights();
    treeEl.querySelectorAll(".fs-dragging").forEach((el) => el.classList.remove("fs-dragging"));
  });
  treeEl.addEventListener("dragover", (ev) => {
    ev.preventDefault(); // required to allow the drop
    const info = dropTargetInfo(ev);
    ev.dataTransfer.dropEffect = isCopyDrop(ev) ? "copy" : "move";
    clearDropHighlights();
    info.el.classList.add("fs-drop-target");
  });
  treeEl.addEventListener("dragleave", (ev) => {
    if (!ev.relatedTarget || !treeEl.contains(ev.relatedTarget)) clearDropHighlights();
  });
  treeEl.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    const info = dropTargetInfo(ev);
    clearDropHighlights();
    if (!info.dir) return;
    // intra-tree drag (move by default, modifier = copy)
    let draggedRel = null;
    try { draggedRel = ev.dataTransfer.getData(DND_TYPE) || null; } catch (e) { draggedRel = null; }
    if (draggedRel) {
      await doDndMoveCopy(draggedRel, info.dir, isCopyDrop(ev));
      return;
    }
    // OS file drop: upload straight into the target folder
    if (ev.dataTransfer.files && ev.dataTransfer.files.length) {
      await doUpload(ev.dataTransfer.files, info.dir);
    }
  });

  document.getElementById("fs-new-file").onclick = () => doCreate("file");
  document.getElementById("fs-new-dir").onclick = () => doCreate("dir");
  document.getElementById("fs-upload-btn").onclick = () => openUploadFor(currentDir());
  document.getElementById("fs-refresh").onclick = () => { refreshDir(currentDir()); refreshDir(""); };
  document.getElementById("fs-internal").onchange = (ev) => {
    state.showInternal = ev.target.checked;
    localStorage.setItem("payload-fs-internal", state.showInternal);
    state.entries.clear();
    state.expanded = new Set([""]);
    state.selectedPath = null;
    state.selectedTable = null;
    state.selectedPaths.clear();
    state.lastClicked = null;
    updateSelectionBar();
    document.getElementById("fs-detail").innerHTML = '<p class="empty-state">Select a file on the left, or use the buttons above to create/upload. Right-click for more actions.</p>';
    loadDir("");
  };
  const uploadInput = document.getElementById("fs-upload-input");
  uploadInput.addEventListener("change", () => {
    const dir = uploadInput.dataset.fsUploadDir || ".";
    doUpload(uploadInput.files, dir);
    uploadInput.value = "";
  });

  /* ---------- content search ---------- */
  const searchInput = document.getElementById("fs-search-input");
  const searchResults = document.getElementById("fs-search-results");
  const runSearch = async () => {
    const q = searchInput.value.trim();
    if (!q) { searchResults.hidden = true; return; }
    searchResults.hidden = false;
    searchResults.innerHTML = '<p class="empty-state m-0">Searching…</p>';
    try {
      const r = await api("/api/fs/search?q=" + encodeURIComponent(q));
      searchResults.innerHTML = r.matches.length
        ? r.matches.map((m) => `
            <button type="button" class="fs-search-result" data-search-path="${escapeHtml(m.path)}" data-search-offset="${m.offset}">
              <span class="offset">${m.offset.toString(16).padStart(4, "0").toUpperCase()}</span>
              <span class="path">${escapeHtml(m.path)}</span>
              <span class="hex">${escapeHtml(m.hex)} ${escapeHtml(m.ascii)}</span>
            </button>`).join("")
        : '<p class="empty-state m-0">No match.</p>';
      searchResults.querySelectorAll("[data-search-path]").forEach((btn) => {
        btn.onclick = async () => {
          const path = btn.dataset.searchPath;
          const offset = Math.max(0, Math.floor(Number(btn.dataset.searchOffset) / 256) * 256);
          try {
            const hex = await api(`/api/fs/read?path=${encodeURIComponent(path)}&as_hex=1&offset=${offset}&limit=256`);
            openHexModal(path, hex);
          } catch (e) {
            toastError(e);
          }
        };
      });
    } catch (e) {
      searchResults.innerHTML = `<p class="empty-state m-0">${escapeHtml(e.message)}</p>`;
    }
  };
  searchInput.addEventListener("keydown", (ev) => { if (ev.key === "Enter") runSearch(); });
  document.getElementById("fs-search-btn").onclick = runSearch;

  /* multi-selection batch actions */
  document.getElementById("fs-sel-move").onclick = () => doBatchMove();
  document.getElementById("fs-sel-copy").onclick = () => doBatchCopy();
  document.getElementById("fs-sel-delete").onclick = () => doBatchDelete();

  /* ---------- initial file from the URL (#/files/<path>) ---------- */
  if (typeof rawPath === "string" && rawPath && rawPath !== "undefined" && rawPath !== "null") {
    // the router already URL-decodes route params
    const initial = rawPath;
    const dir = _dirOf(initial);
    if (dir) {
      state.expanded.add(dir);
      await loadDir(dir);
    }
    renderTree();
    await loadDetail(initial);
  }
}

export { viewFiles };
