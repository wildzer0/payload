/* Dashboard view (route "/"): table summary cards, stat grid, search
 * + cluster/tag filters, the import drag&drop zone, and the
 * "deleted but restorable tables" card. Split out of the former
 * single-file app.js — no behavior change. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, promptDialog, infoDialog, openDialog, statusPill, pageHeader,
  goldBadge, fmtBytes,
} from "../ui.js";
import { api, apiUpload, getPlugins, invalidateTableSources } from "../api.js";



/* A card per table instead of a row in one big HTML table: with 9
 * columns of different information (status, golden, pipeline,
 * sidecar, reader, writer, size, date, snapshot) a rigid table forced
 * every cell into a fixed width and content wrapped, breaking
 * alignment — here every piece of information is a chip that lays
 * itself out (and wraps) on its own via flexbox, without ever
 * breaking the alignment of the rows above/below because there are no
 * columns shared between cards. */
function _importZoneHtml() {
  return `
    <div class="card import-zone">
      <h2>Import table</h2>
      <p class="subtitle">Drag one or more files here to copy them into the project — the tool decides where, no more organizing folders by hand. Multiple files together become a batch table.</p>
      <label class="import-drop" id="import-drop" for="import-file-input">
        ${iconSpan("box")}
        <span>Drag here, or click to choose one or more files</span>
      </label>
      <input type="file" id="import-file-input" multiple hidden>
    </div>`;
}

function _orphanedTablesHtml(names) {
  if (!names.length) return "";
  const rows = names.map((n) => `
    <div class="orphaned-table-row">
      <span class="mono">${escapeHtml(n)}</span>
      <button data-restore-orphan="${escapeHtml(n)}">${iconSpan("refresh")}Restore</button>
    </div>`).join("");
  return `
    <div class="card">
      <h2>Deleted but restorable tables</h2>
      <p class="subtitle">These still have saved history but are no longer on disk (deleted with 'pld rm', the web action, or by hand) — 'Restore' brings them back to the last snapshot.</p>
      <div class="local-plugin-list">${rows}</div>
    </div>`;
}

/* Default reader/writer <select> for a dashboard row: the 'auto'
 * option shows in parentheses what would actually be resolved today
 * (resolvedValue), the other options are the explicit override. */
function _defaultSelectHtml(kind, tableName, options, currentValue, resolvedValue, disabled) {
  const id = `dd-${kind}-${tableName}`;
  const autoLabel = disabled ? "pipeline" : (resolvedValue ? `auto (${resolvedValue})` : "auto");
  const opts = [`<option value="">${escapeHtml(autoLabel)}</option>`].concat(
    options.map((o) => `<option value="${escapeHtml(o)}"${o === currentValue ? " selected" : ""}>${escapeHtml(o)}</option>`)
  );
  return `<select id="${id}" class="inline-select" data-default-kind="${kind}" data-default-table="${escapeHtml(tableName)}"${disabled ? " disabled" : ""}>${opts.join("")}</select>`;
}

function _tableRowHtml(t, stateByName) {
  const state = stateByName[t.name] || "never_saved";
  const tags = t.tags || [];
  const shown = tags.slice(0, 2);
  const extra = tags.length - shown.length;
  // tags under the name, on ONE line (nowrap + ellipsis) so they never
  // stretch the row height
  // the tags line is ALWAYS rendered (min-height in CSS): a table
  // without tags keeps the same row height as one with tags
  const tagsHtml = `<div class="dash-tags"${tags.length ? ` title="${escapeHtml(tags.join(", "))}"` : ""}>${
    tags.length
      ? shown.map((tag) => `<span class="pill pill-dim">${escapeHtml(tag)}</span>`).join("")
        + (extra > 0 ? `<span class="pill pill-dim">+${extra}</span>` : "")
      : ""
  }</div>`;
  const snap = t.last_snapshot ? `snapshot #${t.last_snapshot.id} · ` : "";
  // NOTE: PLAIN template — interpolate built HTML strings directly,
  // never raw()/icon() (they return {__raw} markers -> "[object Object]")
  return `
    <tr>
      <td>
        <a class="link" href="#/table/${encodeURIComponent(t.name)}" title="${escapeHtml(snap)}modified ${escapeHtml(t.source_mtime)}">${escapeHtml(t.name)}</a>
        ${t.is_batch ? `<span class="batch-marker" title="batch table — ${t.source_count} source files">${iconSpan("layers")}</span>` : ""}
        ${tagsHtml}
      </td>
      <td>${t.cluster ? `<span class="pill pill-current" title="Cluster">${escapeHtml(t.cluster)}</span>` : ""}</td>
      <td>${_rawString(statusPill(state))}</td>
      <td>${_rawString(statusPill(t.golden_status || "missing"))}${t.golden_snapshot_id ? goldBadge() : ""}</td>
      <td class="dash-pipeline">
        ${_defaultSelectHtml("reader", t.name, _dashReaderNames, t.reader_override, t.resolved_reader, t.pipeline_explicit)}
        <span class="dash-arrow" aria-hidden="true">→</span>
        ${_defaultSelectHtml("writer", t.name, _dashWriterNames, t.writer_override, t.resolved_writer, t.pipeline_explicit)}
      </td>
      <td class="mono dash-size">${fmtBytes(t.source_size)}${t.output_size != null ? ` → ${fmtBytes(t.output_size)}` : ""}</td>
      <td class="dash-actions">
        <!-- download slot is ALWAYS rendered (visibility-hidden when no
             output): the build button stays at the same x on every row -->
        <a class="btn icon-only${t.output_size != null ? "" : " dash-hidden"}" href="/api/table/${encodeURIComponent(t.name)}/download" title="Download the last built output" download>${iconSpan("download")}</a>
        <button class="icon-only" data-quick-build="${t.name}" title="Quick build (default reader/writer)">${iconSpan("play")}</button>
      </td>
    </tr>`;
}





/* render`...`-helpers like statusPill return raw() markers; in a plain
 * string template they must be unwrapped, otherwise they stringify to
 * "[object Object]". */
const _rawString = (v) => (v && v.__raw !== undefined ? v.__raw : v);

let _dashReaderNames = [];
let _dashWriterNames = [];

async function viewDashboard() {
  const [report, status, plugins, tracked, health] = await Promise.all([
    api("/api/report"), api("/api/status"), getPlugins(), api("/api/log"), api("/api/health"),
  ]);
  const stateByName = Object.fromEntries(status.tables.map((t) => [t.name, t.state]));
  _dashReaderNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
  _dashWriterNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);
  const liveNames = new Set(report.tables.map((t) => t.name));
  const orphanedNames = tracked.tables.filter((n) => !liveNames.has(n));

  const pathByName = Object.fromEntries(status.tables.map((t) => [t.name, t.path]));
  const total = report.tables.length;
  const synced = report.tables.filter((t) => stateByName[t.name] === "clean").length;
  const mismatches = report.tables.filter((t) => t.golden_status === "mismatch" || t.golden_status === "stale").length;
  const dirty = report.tables.filter((t) => stateByName[t.name] === "dirty").length;

  const wireCardHandlers = () => {
    document.querySelectorAll("[data-quick-build]").forEach((btn) => {
      btn.onclick = async () => {
        const table = btn.dataset.quickBuild;
        btn.disabled = true;
        try {
          const r = await api("/api/build", { body: { source: pathByName[table] } });
          toast(`Build of '${table}' completed: ${r.outputs.join(", ")} (${r.was_built ? "rebuilt" : "from cache"})`, "ok");
          viewDashboard();
        } catch (e) {
          toastError(e);
          btn.disabled = false;
        }
      };
    });

    // default reader/writer selects (Pipeline column): persists to the
    // sidecar, same semantics as before
    document.querySelectorAll("[data-default-kind]").forEach((sel) => {
      sel.onchange = async () => {
        const kind = sel.dataset.defaultKind;
        const table = sel.dataset.defaultTable;
        sel.disabled = true;
        try {
          const current = await api("/api/sidecar/" + encodeURIComponent(table));
          const defaults = { ...(current.defaults || {}) };
          if (sel.value) defaults[kind] = sel.value; else delete defaults[kind];
          await api("/api/sidecar/" + encodeURIComponent(table), { method: "PUT", body: { defaults } });

          if (kind === "reader" && sel.value) {
            try {
              const v = await api("/api/source/" + encodeURIComponent(table) + "/validate", { method: "POST" });
              if (v.conforms) {
                toast(`Default reader for '${table}' updated: reads the file correctly`, "ok");
              } else {
                toast(`Reader set, but '${v.reader}' can't read the source of '${table}'`, "warn", v.issues.map((i) => i.detail).join("; "));
              }
            } catch (ve) {
              toast(`Default reader for '${table}' updated (verification failed: ${ve.message})`, "warn");
            }
          } else {
            toast(`Default ${kind === "reader" ? "reader" : "writer"} for '${table}' updated`, "ok");
          }
          viewDashboard();
        } catch (e) {
          toastError(e);
          sel.disabled = false;
        }
      };
    });
  };

  // filter (name/tag/cluster/note/property) + sortable columns
  let filterText = "";
  let sortKey = "name";
  let sortDir = 1; // 1 = ascending, -1 = descending

  const sortHeader = (key, label) => `
    <th class="sortable${sortKey === key ? " sorted" + (sortDir < 0 ? " desc" : "") : ""}" data-sort="${key}">
      ${label}${sortKey === key ? (sortDir < 0 ? " ↓" : " ↑") : ""}
    </th>`;

  const applyView = () => {
    const q = filterText.trim().toLowerCase();
    const rows = report.tables.filter((t) => {
      if (!q) return true;
      const hay = [
        t.name, t.cluster || "", ...(t.tags || []), t.notes || "",
        ...Object.entries(t.properties || {}).map(([k, v]) => `${k} ${v}`),
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
    rows.sort((a, b) => {
      let va, vb;
      if (sortKey === "size") { va = (a.source_size || 0) + (a.output_size || 0); vb = (b.source_size || 0) + (b.output_size || 0); }
      else { va = a[sortKey] != null ? a[sortKey] : ""; vb = b[sortKey] != null ? b[sortKey] : ""; }
      const diff = typeof va === "number" && typeof vb === "number"
        ? va - vb
        : String(va).localeCompare(String(vb));
      return diff * sortDir;
    });
    const countEl = document.getElementById("dash-count");
    if (countEl) countEl.textContent = rows.length === total ? `${total} tables` : `${rows.length} of ${total} tables`;
    document.getElementById("table-list").innerHTML = rows.length
      ? rows.map((t) => _tableRowHtml(t, stateByName)).join("")
      : '<tr><td colspan="7" class="dash-empty">No table matches the current filter.</td></tr>';
    wireCardHandlers();
  };

  const wireSort = () => {
    document.querySelectorAll(".sortable").forEach((th) => {
      th.onclick = () => {
        const key = th.dataset.sort;
        if (sortKey === key) sortDir = -sortDir;
        else { sortKey = key; sortDir = 1; }
        applyView();
      };
    });
  };

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader(health.project_name, `Dashboard · ${total} tables discovered in this project.`))}
    ${raw((report.warnings || []).length
      ? `<div class="card dashboard-warnings">${icon("warnTri")}<div><strong>Project needs attention</strong><ul>${report.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div></div>`
      : "")}
    ${raw(_importZoneHtml())}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Total tables</div><div class="stat-value">${total}</div></div>
      <div class="stat-card"><div class="stat-label">Synced</div><div class="stat-value">${synced}</div></div>
      <div class="stat-card ${mismatches ? "stat-fail" : ""}"><div class="stat-label">Golden mismatch/stale</div><div class="stat-value">${mismatches}</div></div>
      <div class="stat-card ${dirty ? "stat-warn" : ""}"><div class="stat-label">To save</div><div class="stat-value">${dirty}</div></div>
    </div>
    <div class="dash-toolbar">
      <input type="text" id="dash-filter" class="fs-search-input" placeholder="Filter by name, tag, cluster, note or property…">
      <span class="subtitle" id="dash-count"></span>
      <span class="flex-1"></span>
      <a class="btn" href="/api/report/html" target="_blank">${icon("book")}Report</a>
    </div>
    <div class="dash-table-wrap">
      <table class="dash-table">
        <thead><tr>
          ${raw(sortHeader("name", "Table"))}
          <th>Cluster</th>
          <th>Status</th>
          <th>Golden</th>
          <th>Pipeline</th>
          ${raw(sortHeader("size", "Size"))}
          <th class="dash-actions"></th>
        </tr></thead>
        <tbody id="table-list"></tbody>
      </table>
    </div>
    ${raw(_orphanedTablesHtml(orphanedNames))}
  `;

  document.getElementById("dash-filter").addEventListener("input", (ev) => {
    filterText = ev.target.value;
    applyView();
  });
  applyView();
  wireSort();

  const dropZone = document.getElementById("import-drop");
  const importFileInput = document.getElementById("import-file-input");
  ["dragenter", "dragover"].forEach((evt) => dropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    dropZone.classList.add("import-drop-active");
  }));
  ["dragleave", "drop"].forEach((evt) => dropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    dropZone.classList.remove("import-drop-active");
  }));
  dropZone.addEventListener("drop", (ev) => _handleImportFiles(ev.dataTransfer.files));
  importFileInput.addEventListener("change", () => {
    _handleImportFiles(importFileInput.files);
    importFileInput.value = "";
  });

  document.querySelectorAll("[data-restore-orphan]").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.restoreOrphan;
      btn.disabled = true;
      try {
        const preview = await api("/api/restore", { body: { table_name: name } });
        const batchNote = preview.recreate_batch_entry ? " Its [[batch_table]] entry will also be re-added to table-tool.toml." : "";
        const ok = await confirmDialog(`'${name}' will be recreated from the last snapshot (#${preview.snapshot_id}).${batchNote}`, { confirmLabel: "Restore" });
        if (!ok) { btn.disabled = false; return; }
        const r = await api("/api/restore", { body: { table_name: name, snapshot_id: preview.snapshot_id, confirm: true } });
        toast(`'${name}' restored`, "ok");
        if (r.pipeline_warning) toast(r.pipeline_warning, "warn");
        viewDashboard();
      } catch (e) {
        toastError(e);
        btn.disabled = false;
      }
    };
  });
}

/* Asked only when 2+ files are dropped together: one batch table made
 * of all of them, or each file imported as its own standalone table
 * (for a pile of unrelated files — see BATCH.md vs the '--each' case
 * of 'pld import' in cli.py import_cmd). Resolves to
 * { mode: "batch" | "each", overwrite } or null if cancelled. */
function _chooseImportMode(count) {
  return openDialog({
    title: "Import mode",
    body: render`
      <p>${count} files dropped together. Create ONE batch table made of all of them, or import each as its own, independent table?</p>
      <label class="toggle-chip"><input type="checkbox" id="modal-each-overwrite"><span>Overwrite tables that already exist</span></label>
    `,
    cancelValue: null,
    actions: [
      { label: "Cancel", value: null },
      { label: "One batch table", value: { mode: "batch", overwrite: false } },
      {
        label: `${count} separate tables`,
        className: "primary",
        value: () => ({ mode: "each", overwrite: document.getElementById("modal-each-overwrite").checked }),
      },
    ],
  });
}

async function _reportBulkImport(r) {
  const importedCount = r.imported.length;
  const skipped = r.skipped || [];
  if (!skipped.length) {
    toast(`${importedCount} table${importedCount === 1 ? "" : "s"} imported`, "ok");
    return;
  }
  const rows = skipped.map((s) => `<li><code>${escapeHtml(s.filename)}</code> — ${escapeHtml(s.reason)}</li>`).join("");
  await infoDialog(`
    <p>${importedCount} table${importedCount === 1 ? "" : "s"} imported, ${skipped.length} skipped:</p>
    <ul class="bulk-import-skip-list">${rows}</ul>
  `);
}

/* One or more files dragged/chosen in the dashboard's drop zone — a
 * single file asks for the table name (default: filename without
 * extension); multiple files together ask whether to bundle them into
 * one new batch table or import each as its own independent table
 * (same choice as the CLI: 'pld import <path> [--as name]' vs
 * '--new-batch name' vs '--each', see cli.py import_cmd). A name that
 * already exists offers to overwrite instead of silently refusing
 * (single-file case); the bulk/'each' case skips existing names
 * instead and reports them, unless 'overwrite' is checked. */
async function _handleImportFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;

  const formData = new FormData();
  if (files.length === 1) {
    const defaultName = files[0].name.replace(/\.[^.]+$/, "");
    const name = await promptDialog(`Table name for '${files[0].name}'?`, { value: defaultName, confirmLabel: "Import" });
    if (name === null) return;
    formData.append("file", files[0]);
    if (name !== defaultName) formData.append("as_name", name);
  } else {
    const choice = await _chooseImportMode(files.length);
    if (!choice) return;
    files.forEach((f) => formData.append("file", f));
    if (choice.mode === "batch") {
      const batchName = await promptDialog(`Name of the new batch table for these ${files.length} files?`, { placeholder: "batch_name", confirmLabel: "Import" });
      if (!batchName) return;
      formData.append("new_batch", batchName);
    } else {
      formData.append("each", "true");
      if (choice.overwrite) formData.append("overwrite", "true");
    }
  }

  try {
    const r = await apiUpload("/api/table/import", formData);
    invalidateTableSources(); // new tables appeared
    if (r.kind === "bulk") {
      await _reportBulkImport(r);
    } else {
      toast(`Import completed: ${r.kind === "batch" ? r.name : r.path}`, "ok");
    }
    viewDashboard();
  } catch (e) {
    if (e.data && e.data.error === "TableAlreadyExistsError" && files.length === 1) {
      const ok = await confirmDialog(`${e.message}. Do you want to overwrite its content?`, { danger: true, confirmLabel: "Overwrite" });
      if (!ok) return;
      formData.append("overwrite", "true");
      try {
        const r2 = await apiUpload("/api/table/import", formData);
        toast(`'${r2.path}' updated`, "ok");
        viewDashboard();
      } catch (e2) {
        toastError(e2);
      }
      return;
    }
    toastError(e);
  }
}

export { viewDashboard };
