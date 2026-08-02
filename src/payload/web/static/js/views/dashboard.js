/* Dashboard view (route "/"): table summary cards, stat grid, search
 * + cluster/tag filters, the import drag&drop zone, and the
 * "deleted but restorable tables" card. Split out of the former
 * single-file app.js — no behavior change. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, promptDialog, infoDialog, statusPill, pageHeader,
  goldBadge, fmtBytes, fmtShortTimestamp, debounce,
} from "../ui.js";
import { api, apiUpload, getPlugins } from "../api.js";

/* Default reader/writer <select> for a dashboard row: the 'auto'
 * option shows in parentheses what would actually be resolved today
 * (resolvedValue), the other options are the explicit override — a
 * single control covers both "what happens" and "what I chose".
 * Disabled (with a separate badge in the table) if the table has an
 * explicit pipeline: in that case default reader/writer don't apply
 * at all, they're ignored by resolution. */
function _defaultSelectHtml(kind, tableName, options, currentValue, resolvedValue, disabled) {
  const id = `dd-${kind}-${tableName}`;
  const autoLabel = disabled ? "pipeline" : (resolvedValue ? `auto (${resolvedValue})` : "auto");
  const opts = [`<option value="">${escapeHtml(autoLabel)}</option>`].concat(
    options.map((o) => `<option value="${escapeHtml(o)}"${o === currentValue ? " selected" : ""}>${escapeHtml(o)}</option>`)
  );
  return `<select id="${id}" class="inline-select" data-default-kind="${kind}" data-default-table="${escapeHtml(tableName)}"${disabled ? " disabled" : ""}>${opts.join("")}</select>`;
}

/* Highlights the "current" snapshot (accent, same treatment as the
 * table page) and, if the history HEAD is further ahead (the table is
 * stuck at an earlier restore), flags it with a secondary chip —
 * otherwise the fact that more recent, never-"reactivated" snapshots
 * exist would disappear. Both the empty state and the "behind HEAD"
 * state are kept to short, non-wrapping content (icon + at most a
 * number) with the explanation moved into the title tooltip, since
 * this chip lives in a narrow grid cell where multi-word labels wrap
 * mid-phrase and look broken. */
function _snapshotChipHtml(t) {
  if (!t.last_snapshot) {
    return `<span class="pill pill-dim" title="No snapshot saved yet">${iconSpan("dash")}—</span>`;
  }
  const current = `<span class="pill pill-current">#${t.last_snapshot.id}</span>`;
  const behindTip = t.tip_snapshot_id && t.tip_snapshot_id !== t.last_snapshot.id;
  const tipNote = behindTip
    ? `<span class="pill pill-dim" title="History goes up to snapshot #${t.tip_snapshot_id}, this table is showing an earlier restore">${iconSpan("warnTri")}#${t.tip_snapshot_id}</span>`
    : "";
  return current + tipNote;
}

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

function _tableCardHtml(t, stateByName) {
  return render`
    <div class="table-summary-card">
      <div class="table-summary-head">
        <a class="link table-summary-name" href="#/table/${t.name}">${t.name}</a>
        <div class="table-summary-actions">
          <div class="table-summary-badges">
            ${statusPill(stateByName[t.name] || "never_saved")}
            ${statusPill(t.golden_status || "missing")}
            ${raw(t.golden_snapshot_id ? goldBadge() : "")}
            ${raw(t.pipeline_explicit ? '<span class="pill pill-warn" title="Explicit pipeline configured: overrides default reader/writer">pipeline</span>' : "")}
            ${raw(t.has_sidecar ? '<span class="pill pill-dim" title="Sidecar (<name>.config.toml) active for this table">override</span>' : "")}
            ${raw(t.cluster ? `<span class="pill pill-current" title="Cluster">${escapeHtml(t.cluster)}</span>` : "")}
          </div>
          ${raw(t.output_size != null
            ? `<a class="btn icon-only" href="/api/table/${encodeURIComponent(t.name)}/download" title="Download the last built output" download>${iconSpan("download")}</a>`
            : "")}
          <button class="icon-only" data-quick-build="${t.name}" title="Quick build (uses default reader/writer, no other parameter)">${icon("play")}</button>
        </div>
      </div>
      <div class="table-summary-meta">
        <span class="meta-chip meta-chip-control"><strong>Reader</strong>${raw(_defaultSelectHtml("reader", t.name, _dashReaderNames, t.reader_override, t.resolved_reader, t.pipeline_explicit))}</span>
        <span class="meta-chip meta-chip-control"><strong>Writer</strong>${raw(_defaultSelectHtml("writer", t.name, _dashWriterNames, t.writer_override, t.resolved_writer, t.pipeline_explicit))}</span>
        <span class="meta-chip"><strong>Size</strong><span class="mono">${fmtBytes(t.source_size)} → ${fmtBytes(t.output_size)}</span></span>
        <span class="meta-chip" title="${t.source_mtime}"><strong>Modified</strong><span class="mono">${fmtShortTimestamp(t.source_mtime)}</span></span>
        <span class="meta-chip"><strong>Snapshot</strong>${raw(_snapshotChipHtml(t))}</span>
      </div>
      ${raw(t.tags && t.tags.length
        ? `<div class="table-summary-tags">${t.tags.map((tag) => `<span class="pill pill-dim">${escapeHtml(tag)}</span>`).join("")}</div>`
        : "")}
    </div>
  `;
}

/* Search box (name/tags substring) + cluster chip row (single active
 * selection, like the Plugins page's kind filter) + tag chip row
 * (MULTIPLE simultaneously active, OR semantics — a table matching
 * ANY active tag passes, more natural for a "quick search" aid than
 * requiring every tag at once). Returns "" (nothing rendered) if the
 * project uses neither clusters nor tags, so a project not using this
 * feature sees no extra UI at all. */
function _dashboardFilterHtml(tables) {
  const clusterNames = [...new Set(tables.map((t) => t.cluster).filter(Boolean))].sort();
  const tagCounts = {};
  for (const t of tables) for (const tag of t.tags || []) tagCounts[tag] = (tagCounts[tag] || 0) + 1;
  const tagNames = Object.keys(tagCounts).sort();
  if (!clusterNames.length && !tagNames.length) return "";

  const clusterRow = clusterNames.length ? `
    <div class="toggle-chip-row" id="dash-cluster-filters">
      <button type="button" class="toggle-chip dash-cluster-chip active" data-cluster-filter="">All clusters</button>
      ${clusterNames.map((c) => `<button type="button" class="toggle-chip dash-cluster-chip" data-cluster-filter="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join("")}
    </div>` : "";

  const tagRow = tagNames.length ? `
    <div class="toggle-chip-row" id="dash-tag-filters">
      ${tagNames.map((tag) => `<button type="button" class="toggle-chip dash-tag-chip" data-tag-filter="${escapeHtml(tag)}">${escapeHtml(tag)} <span class="pill pill-dim">${tagCounts[tag]}</span></button>`).join("")}
    </div>` : "";

  return `
    <div class="card dashboard-filters">
      <input type="text" id="dash-search" class="dash-search-input" placeholder="Search tables by name or tag…">
      ${clusterRow}
      ${tagRow}
    </div>`;
}

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

  let searchQuery = "";
  let clusterFilter = "";
  const activeTags = new Set();

  const matchesFilters = (t) => {
    if (clusterFilter && t.cluster !== clusterFilter) return false;
    if (activeTags.size && !(t.tags || []).some((tag) => activeTags.has(tag))) return false;
    if (searchQuery) {
      const haystack = `${t.name} ${(t.tags || []).join(" ")}`.toLowerCase();
      if (!haystack.includes(searchQuery)) return false;
    }
    return true;
  };

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
            // the chosen reader exists, but does it REALLY read this
            // table's file? same check as 'Validate conformance' on
            // plugins, here pointed at the reader just set as default.
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

  const renderCards = () => {
    const filtered = report.tables.filter(matchesFilters);
    const cards = filtered.map((t) => _tableCardHtml(t, stateByName));
    document.getElementById("table-summary-list").innerHTML =
      cards.length ? cards.join("") : '<p class="empty-state card">No table matches this filter.</p>';
    wireCardHandlers();
  };

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader(health.project_name, `Dashboard · ${total} tables discovered in this project.`))}
    ${raw(_importZoneHtml())}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Total tables</div><div class="stat-value">${total}</div></div>
      <div class="stat-card"><div class="stat-label">Synced</div><div class="stat-value">${synced}</div></div>
      <div class="stat-card ${mismatches ? "stat-fail" : ""}"><div class="stat-label">Golden mismatch/stale</div><div class="stat-value">${mismatches}</div></div>
      <div class="stat-card ${dirty ? "stat-warn" : ""}"><div class="stat-label">To save</div><div class="stat-value">${dirty}</div></div>
    </div>
    ${raw(_dashboardFilterHtml(report.tables))}
    <div class="table-summary-list" id="table-summary-list"></div>
    ${raw(_orphanedTablesHtml(orphanedNames))}
  `;

  renderCards();

  const searchInput = document.getElementById("dash-search");
  if (searchInput) {
    searchInput.oninput = debounce(() => {
      searchQuery = searchInput.value.trim().toLowerCase();
      renderCards();
    }, 150);
  }
  document.querySelectorAll("#dash-cluster-filters [data-cluster-filter]").forEach((btn) => {
    btn.onclick = () => {
      clusterFilter = btn.dataset.clusterFilter;
      document.querySelectorAll("#dash-cluster-filters [data-cluster-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      renderCards();
    };
  });
  document.querySelectorAll("#dash-tag-filters [data-tag-filter]").forEach((btn) => {
    btn.onclick = () => {
      const tag = btn.dataset.tagFilter;
      if (activeTags.has(tag)) {
        activeTags.delete(tag);
        btn.classList.remove("active");
      } else {
        activeTags.add(tag);
        btn.classList.add("active");
      }
      renderCards();
    };
  });

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
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  return new Promise((resolveFn) => {
    box.innerHTML = render`
      <p>${count} files dropped together. Create ONE batch table made of all of them, or import each as its own, independent table?</p>
      <label class="toggle-chip"><input type="checkbox" id="modal-each-overwrite"><span>Overwrite tables that already exist</span></label>
      <div class="modal-actions">
        <button type="button" id="modal-cancel">Cancel</button>
        <button type="button" id="modal-mode-batch">One batch table</button>
        <button type="button" class="primary" id="modal-mode-each">${count} separate tables</button>
      </div>
    `;
    overlay.hidden = false;
    const overwriteBox = box.querySelector("#modal-each-overwrite");
    const cleanup = (result) => { overlay.hidden = true; resolveFn(result); };
    box.querySelector("#modal-cancel").onclick = () => cleanup(null);
    box.querySelector("#modal-mode-batch").onclick = () => cleanup({ mode: "batch", overwrite: false });
    box.querySelector("#modal-mode-each").onclick = () => cleanup({ mode: "each", overwrite: overwriteBox.checked });
    overlay.onclick = (ev) => { if (ev.target === overlay) cleanup(null); };
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
