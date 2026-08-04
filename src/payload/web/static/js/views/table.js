/* Table detail view (route "/table/<name>"): build form, source
 * content editor, visual pipeline builder, sidecar card, tags &
 * cluster, history (commit/log/restore/golden). Split out of the
 * former single-file app.js — no behavior change. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, ICONS, toast, toastError,
  confirmDialog, promptDialog, openDialog, openContextMenu, statusPill, pageHeader, pinnedCard,
  goldBadge, currentBadge, baseName, fmtBytes, val, chk, attachAutocomplete,
  registerDirtyGuard, removeDirtyGuard, loadCodeMirror, emptyCard, openTextEditorModal,
} from "../ui.js";
import { api, getPlugins, ensureTableSources, findSourcePath, invalidateTableSources, isBatchTable } from "../api.js";
import { openPipelineEditor } from "./pipeline_editor.js";

const COMMIT_MESSAGE_MAX_LENGTH = 1024;
const HISTORY_PAGE_SIZE = 4;

async function viewTable(name) {
  const content = document.getElementById("content");
  document.addEventListener("pipeline-saved", () => loadPipelineBuilder(name));

  const buildBody = `
    <div class="field-row">
      <div class="field">
        <label>Writer</label>
        <div class="autocomplete-wrap"><input type="text" id="f-to" placeholder="default"></div>
      </div>
      <div class="field">
        <label>Reader</label>
        <div class="autocomplete-wrap"><input type="text" id="f-from" placeholder="auto"></div>
      </div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="f-force"><span>--force</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-dry"><span>--dry-run</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-golden"><span>--check-golden</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-preview"><span>--preview-diff</span></label>
    </div>
    <div class="build-actions">
      <button class="primary" id="btn-build">${iconSpan("play")}Build</button>
      <span class="subtitle">Empty fields use the table's configured defaults.</span>
    </div>
    <div class="toggle-chip-row inspect-row">
      <button id="btn-diff-snapshot">${iconSpan("edit")}Diff vs snapshot</button>
      <button id="btn-diff-golden">${iconSpan("star")}Diff vs golden</button>
      <button id="btn-analyze-output">${iconSpan("box")}Analyze output</button>
    </div>
    <div id="inspect-result"></div>
    <div id="build-result"></div>
  `;

  const commitBody = `
    <div id="golden-summary" class="mb-14"></div>
    <div class="field">
      <label>Commit message</label>
      <textarea id="commit-message" class="commit-message-input mono" rows="3" maxlength="${COMMIT_MESSAGE_MAX_LENGTH}" placeholder="Describe what changed…"></textarea>
      <div class="field-hint"><span id="commit-message-count">0</span>/${COMMIT_MESSAGE_MAX_LENGTH}</div>
    </div>
    <label class="commit-golden-row">
      <span class="switch switch-lg"><input type="checkbox" id="commit-golden"><span class="track"></span></span>
      <span class="commit-golden-label">
        <span class="commit-golden-title">${iconSpan("star")}Also set as golden</span>
        <small>Save this snapshot as the golden reference for future diff checks</small>
      </span>
    </label>
    <div class="build-actions">
      <button id="btn-commit">${iconSpan("save")}Commit changes</button>
    </div>
  `;

  const headerActionsHtml = `
    <a class="btn icon-only" id="btn-download-output" href="/api/table/${encodeURIComponent(name)}/download" title="Download the last built output" download hidden>${iconSpan("download")}</a>
    <button id="btn-rename-table">${iconSpan("edit")}Rename</button>
    <button id="btn-clone-table">${iconSpan("copy")}Clone</button>
    <button class="danger" id="btn-delete-table">${iconSpan("trash")}Delete table</button>
  `;

  const tagsClusterBody = `
    <div class="field">
      <label>Cluster</label>
      <select id="tc-cluster" class="inline-select w-full max-w-none"><option value="">— none —</option></select>
    </div>
    <div class="field">
      <label>Tags</label>
      <div id="tc-tag-chips" class="table-summary-tags"></div>
      <input type="text" id="tc-tag-input" placeholder="Add a tag, press Enter" class="mt-8">
    </div>
    <div class="field">
      <label>Notes</label>
      <textarea id="tc-notes" class="commit-message-input mono" rows="3" placeholder="Free-form notes about this table…"></textarea>
    </div>
    <div class="field">
      <label>Properties</label>
      <div id="tc-props" class="tc-props"></div>
      <div class="tc-prop-add">
        <input type="text" id="tc-prop-key" class="mono" placeholder="key, e.g. address">
        <input type="text" id="tc-prop-value" class="mono" placeholder="value, e.g. 0x8000">
        <button id="tc-prop-add-btn">${iconSpan("plus")}Add</button>
      </div>
    </div>
    <div class="build-actions">
      <button id="tc-save-meta">${iconSpan("save")}Save notes & properties</button>
    </div>
  `;

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/">← Dashboard</a></div>
    ${raw(pageHeader(name, undefined, headerActionsHtml))}
    <div class="table-detail-layout">
      <div class="table-detail-main">
        ${raw(pinnedCard("Build", buildBody))}
        ${raw(pinnedCard("Source content", '<div id="view-result"><p class="empty-state">—</p></div>'))}
        ${raw(pinnedCard("Pipeline", '<div id="pipeline-result"></div>'))}
        ${raw(pinnedCard("Table-specific config (sidecar)", '<div id="sidecar-result"></div>'))}
      </div>
      <div class="table-detail-side">
        ${raw(pinnedCard("Table info", tagsClusterBody))}
        ${raw(pinnedCard("Commit changes", commitBody))}
        ${raw(pinnedCard("History", '<div id="history-result"></div>'))}
      </div>
    </div>
  `;

  getPlugins().then((plugins) => {
    const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
    const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);
    attachAutocomplete(document.getElementById("f-from"), () => readerNames);
    attachAutocomplete(document.getElementById("f-to"), () => writerNames);
  }).catch(() => {});

  // The --from/--to placeholders reflect the default reader/writer
  // already set for this table (dashboard or sidecar) — leaving the
  // field empty will really use that default, writing something in it
  // only overrides it for THIS build, without touching the saved default.
  // Combined with the cluster dropdown's population (from /api/clusters)
  // in one Promise.all: setting <select>.value before its <option>s
  // exist (a race if these ran in two separate .then()s) would silently
  // fail to select anything.
  let tcTags = [];
  const renderTagChips = () => {
    const chips = tcTags.map((tag) => `
      <span class="pill pill-dim">${escapeHtml(tag)} <button type="button" class="chip-remove" data-remove-tag="${escapeHtml(tag)}" aria-label="Remove tag">×</button></span>
    `).join("");
    document.getElementById("tc-tag-chips").innerHTML = chips || '<span class="table-tags-empty">No tags yet</span>';
    document.querySelectorAll("[data-remove-tag]").forEach((btn) => {
      btn.onclick = async () => {
        tcTags = tcTags.filter((t) => t !== btn.dataset.removeTag);
        await saveTags();
      };
    });
  };
  const saveTags = async () => {
    try {
      await api(`/api/table/${encodeURIComponent(name)}/tags`, { method: "PUT", body: { tags: tcTags } });
      renderTagChips();
    } catch (e) {
      toastError(e);
    }
  };
  document.getElementById("tc-tag-input").addEventListener("keydown", async (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const value = ev.target.value.trim();
    if (!value) return;
    if (!tcTags.includes(value)) tcTags.push(value);
    ev.target.value = "";
    await saveTags();
  });
  document.getElementById("tc-cluster").onchange = async (ev) => {
    const cluster = ev.target.value || null;
    try {
      await api(`/api/table/${encodeURIComponent(name)}/cluster`, { method: "PUT", body: { cluster } });
      toast(cluster ? `Assigned to cluster '${cluster}'` : "No longer in a cluster", "ok");
    } catch (e) {
      toastError(e);
    }
  };

  // notes + custom properties (key/value pairs) — edited together,
  // saved with one button; tags/cluster keep their auto-save behavior
  let tcProps = {};
  const renderProps = () => {
    const el = document.getElementById("tc-props");
    const rows = Object.entries(tcProps).map(([k, v]) => `
      <div class="tc-prop-row">
        <input type="text" class="tc-prop-key mono" value="${escapeHtml(k)}" aria-label="Property key">
        <input type="text" class="tc-prop-value mono" value="${escapeHtml(v)}" aria-label="Property value">
        <button type="button" class="chip-remove" aria-label="Remove property">×</button>
      </div>`).join("");
    el.innerHTML = rows || '<span class="table-tags-empty">No custom properties</span>';
    const collect = () => {
      tcProps = {};
      el.querySelectorAll(".tc-prop-row").forEach((row) => {
        const key = row.querySelector(".tc-prop-key").value.trim();
        if (key) tcProps[key] = row.querySelector(".tc-prop-value").value;
      });
    };
    el.querySelectorAll(".tc-prop-row").forEach((row) => {
      row.querySelector(".tc-prop-key").addEventListener("input", collect);
      row.querySelector(".tc-prop-value").addEventListener("input", collect);
      row.querySelector(".chip-remove").onclick = () => { row.remove(); collect(); };
    });
  };
  const addProp = () => {
    const key = document.getElementById("tc-prop-key").value.trim();
    if (!key) { toast("Property key can't be empty", "warn"); return; }
    tcProps[key] = document.getElementById("tc-prop-value").value;
    document.getElementById("tc-prop-key").value = "";
    document.getElementById("tc-prop-value").value = "";
    renderProps();
  };
  document.getElementById("tc-prop-add-btn").onclick = addProp;
  document.getElementById("tc-prop-value").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addProp(); }
  });
  document.getElementById("tc-save-meta").onclick = async () => {
    try {
      await api(`/api/table/${encodeURIComponent(name)}/meta`, {
        method: "PUT",
        body: { notes: document.getElementById("tc-notes").value, properties: tcProps },
      });
      toast("Table info saved", "ok");
    } catch (e) {
      toastError(e);
    }
  };

  Promise.all([
    api("/api/clusters"),
    api("/api/report"),
    api(`/api/table/${encodeURIComponent(name)}/meta`),
  ]).then(([clustersResp, report, meta]) => {
    const sel = document.getElementById("tc-cluster");
    clustersResp.clusters.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.name;
      opt.textContent = c.name;
      sel.appendChild(opt);
    });

    const row = report.tables.find((t) => t.name === name);
    if (!row) return;
    sel.value = row.cluster || "";
    tcTags = row.tags || [];
    renderTagChips();
    document.getElementById("tc-notes").value = meta.notes || "";
    tcProps = meta.properties || {};
    renderProps();

    if (row.output_size != null) document.getElementById("btn-download-output").hidden = false;
    if (row.pipeline_explicit) return;
    if (row.resolved_reader) document.getElementById("f-from").placeholder = row.resolved_reader;
    if (row.resolved_writer) document.getElementById("f-to").placeholder = row.resolved_writer;
  }).catch(() => {});

  document.getElementById("btn-build").onclick = async () => {
    if (chk("f-preview")) {
      try {
        const r = await api(`/api/table/${encodeURIComponent(name)}/preview-diff`, {
          body: { from: val("f-from") || undefined, to: val("f-to") || undefined },
        });
        await renderPreviewDiff(r);
      } catch (e) {
        toastError(e);
      }
      return;
    }
    const body = {
      source: findSourcePath(name), to: val("f-to") || undefined, from: val("f-from") || undefined,
      force: chk("f-force"), dry_run: chk("f-dry"), check_golden: chk("f-golden"),
    };
    try {
      const r = await api("/api/build", { body });
      const status = r.dry_run
        ? (r.was_built ? "dry-run: would be rebuilt, no file written" : "dry-run: would use the cache, no file written")
        : (r.was_built ? "rebuilt" : "from cache");
      if (!r.dry_run) document.getElementById("btn-download-output").hidden = false;
      toast(`Build ok: ${r.outputs.join(", ")} (${status})`, "ok");
      loadPipelineBuilder(name);
    } catch (e) {
      toastError(e);
    }
  };

  /* ---------- live build-diff (--preview-diff) ---------- */
  const renderPreviewDiff = async (r) => {
    // the summary is a disappearing toast — never inline (it used to
    // render into the Build card and broke the page layout)
    const baselineLabel = r.baseline === "golden" ? `golden #${r.golden_snapshot_id}` : "current output";
    const newFiles = r.outputs.filter((o) => o.new_file).length;
    const changedRuns = r.outputs.reduce((n, o) => n + o.runs.length, 0);
    if (!r.outputs.length) {
      toast("Preview produced no output — check the reader/writer resolution", "warn");
      return;
    }
    if (!newFiles && !changedRuns) {
      toast(`Preview: identical to the ${baselineLabel}`, "ok");
      return;
    }
    if (newFiles && !changedRuns) {
      toast(`Preview: ${newFiles} new file${newFiles === 1 ? "" : "s"} — first build, nothing to compare`, "warn");
    } else {
      toast(`Preview: ${changedRuns} byte run${changedRuns === 1 ? "" : "s"} differ${newFiles ? ` + ${newFiles} new` : ""} vs ${baselineLabel}`, "warn");
    }
    openPreviewCompare(r);
  };

  /* Side-by-side blob compare (modal): previous vs current hex, with
   * the differing lines highlighted and the reader's comments. */
  const openPreviewCompare = (r) => {
    const overlay = document.getElementById("modal-overlay");
    const box = document.getElementById("modal-box");
    const baselineLabel = r.baseline === "golden" ? `golden #${r.golden_snapshot_id}` : "current output";
    box.classList.add("modal-large");
    box.innerHTML = render`
      <div class="preview-compare">
        <div class="preview-compare-head">
          <h3 class="m-0">Preview vs ${baselineLabel}</h3>
          <button class="modal-x modal-x-static" id="btn-preview-x" type="button" aria-label="Close" title="Close">×</button>
          <span class="subtitle">Changed lines are highlighted: <span class="cmp-legend-l">previous</span> → <span class="cmp-legend-r">new</span></span>
          <span class="flex-1"></span>
          <button class="primary" id="btn-preview-accept">${icon("save")}Accept & commit</button>
          <button class="ghost" id="btn-preview-discard">${icon("close")}Discard</button>
        </div>
        <div class="preview-compare-body">
          ${raw(r.outputs.filter((o) => o.new_file || o.runs.length).map((o) => outputCompareHtml(o, r)).join(""))}
        </div>
      </div>
    `;
    overlay.hidden = false;

    // synchronized scrolling between the two panes of each file
    box.querySelectorAll(".cmp-file").forEach((wrap) => {
      const left = wrap.querySelector(".cmp-left");
      const right = wrap.querySelector(".cmp-right");
      if (left && right) {
        const sync = (ev, other) => { other.scrollTop = ev.target.scrollTop; };
        left.addEventListener("scroll", (ev) => { right.scrollTop = ev.target.scrollTop; });
        right.addEventListener("scroll", (ev) => { left.scrollTop = ev.target.scrollTop; });
      }
    });

    const close = () => { overlay.hidden = true; box.classList.remove("modal-large"); box.innerHTML = ""; };
    document.getElementById("btn-preview-accept").onclick = async () => {
      const btn = document.getElementById("btn-preview-accept");
      btn.disabled = true;
      try {
        const message = (document.getElementById("commit-message").value || "").trim() || "Accept preview build";
        await api("/api/build", {
          body: { source: findSourcePath(name), to: val("f-to") || undefined, from: val("f-from") || undefined, force: true },
        });
        await api("/api/commit", { body: { message, only: [name] } });
        toast("Preview accepted and committed", "ok");
        close();
        loadPipelineBuilder(name);
        loadHistory(name);
      } catch (e) {
        toastError(e);
        btn.disabled = false;
      }
    };
    document.getElementById("btn-preview-discard").onclick = close;
    const previewX = document.getElementById("btn-preview-x");
    if (previewX) previewX.onclick = close;
  };

  // hex+ascii lines for one side of the compare (capped so a huge
  // blob can't build a multi-megabyte DOM)
  const MAX_CMP_LINES = 600;
  const hexLines = (b64) => {
    const bytes = atob(b64 || "");
    const lines = [];
    const n = Math.min(bytes.length, MAX_CMP_LINES * 8);
    for (let i = 0; i < n; i += 8) {
      const chunk = bytes.slice(i, i + 8);
      const hex = Array.from(chunk).map((c) => c.charCodeAt(0).toString(16).padStart(2, "0")).join(" ");
      const ascii = Array.from(chunk).map((c) => (c.charCodeAt(0) >= 32 && c.charCodeAt(0) <= 126 ? c : ".")).join("");
      lines.push({ hex, ascii });
    }
    return lines;
  };

  const cmpTable = (lines, diffIdx, isNew) => {
    if (isNew) return `<p class="empty-state m-0">— no previous output —</p>`;
    return `<table class="cmp-table"><tbody>${
      lines.map((ln, i) => `
        <tr${diffIdx.has(i) ? ` class="cmp-diff"` : ""}>
          <td class="cmp-off">0x${(i * 8).toString(16).padStart(4, "0").toUpperCase()}</td>
          <td class="cmp-hex">${escapeHtml(ln.hex)}</td>
          <td class="cmp-ascii">${escapeHtml(ln.ascii)}</td>
        </tr>`).join("")
    }</tbody></table>`;
  };

  const outputCompareHtml = (o) => {
    const prevLines = hexLines(o.prev_base64);
    const curLines = hexLines(o.cur_base64);
    const diffIdx = new Set(o.runs.map((run) => run.offset / 8));
    const n = Math.max(prevLines.length, curLines.length);
    // pad the shorter side to the same line count so the two panes stay
    // in sync while scrolling
    const pad = (lines) => { while (lines.length < n) lines.push({ hex: "", ascii: "" }); return lines; };
    const left = pad(prevLines.slice());
    const right = pad(curLines.slice());
    const truncated = o.size > MAX_CMP_LINES * 8;
    return `
      <div class="cmp-file">
        <div class="cmp-file-head">
          <strong>${escapeHtml(o.filename)}</strong>
          ${o.new_file ? '<span class="pill pill-warn">new file</span>' : ""}
          <span class="subtitle mono">${o.runs.length} run${o.runs.length === 1 ? "" : "s"} differ</span>
        </div>
        ${truncated ? `<p class="subtitle m-0">Showing the first ${MAX_CMP_LINES} rows (${o.size} bytes total).</p>` : ""}
        <div class="cmp-panes">
          <div class="cmp-pane cmp-left">${cmpTable(left, diffIdx, o.new_file)}</div>
          <div class="cmp-pane cmp-right">${cmpTable(right, diffIdx, false)}</div>
        </div>
      </div>`;
  };

  /* ---------- inspect: diff vs snapshot/golden, analyze output ---------- */  /* ---------- inspect: diff vs snapshot/golden, analyze output ---------- */  /* ---------- inspect: diff vs snapshot/golden, analyze output ---------- */
  const inspectResult = document.getElementById("inspect-result");

  const renderDiff = (r, labelRef) => {
    const files = r.files || Object.entries(r.diffs || {}).map(([filename, chunks]) => ({ filename, chunks }));
    const identical = r.identical != null ? r.identical : !files.length;
    if (identical) {
      inspectResult.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>Identical — no difference${r.snapshot_id != null ? ` vs snapshot #${r.snapshot_id}` : ""}.</span></div>`;
      return;
    }
    const rows = files.map((f) => `
      <div class="diff-file">
        <div class="diff-file-name">${escapeHtml(f.filename)}</div>
        ${f.chunks.map((c) => `
          <div class="diff-row">
            <span class="diff-off mono">0x${c.offset.toString(16).padStart(4, "0").toUpperCase()}</span>
            <span class="diff-cur mono">${escapeHtml(c.current)}</span>
            <span class="diff-arrow">→</span>
            <span class="diff-ref mono">${escapeHtml((c.snapshot != null ? c.snapshot : c.golden))}</span>
          </div>`).join("")}
      </div>`).join("");
    inspectResult.innerHTML = render`
      <div class="result-line">${statusPill("mismatch")}<span>${files.length} file(s) differ — current vs ${labelRef}.</span></div>
      <div class="diff-list">${raw(rows)}</div>
    `;
  };

  const renderAnalyze = (a) => {
    const counts = new Map(a.freq);
    const top = [...counts.entries()].sort((x, y) => y[1] - x[1]).slice(0, 32);
    const max = top.length ? top[0][1] : 1;
    inspectResult.innerHTML = render`
      <div class="result-line"><span class="subtitle mono">${a.path}</span></div>
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
  };

  const inspectWith = async (label, fetcher) => {
    inspectResult.innerHTML = '<p class="empty-state m-0">Loading…</p>';
    try {
      renderDiff(await fetcher(), label);
    } catch (e) {
      inspectResult.innerHTML = emptyCard(e.message, e.hint);
    }
  };
  document.getElementById("btn-diff-snapshot").onclick = () =>
    inspectWith("snapshot", () => api("/api/diff/" + encodeURIComponent(name)));
  document.getElementById("btn-diff-golden").onclick = () =>
    inspectWith("golden", () => api("/api/golden/" + encodeURIComponent(name) + "/diff"));
  document.getElementById("btn-analyze-output").onclick = async () => {
    inspectResult.innerHTML = '<p class="empty-state m-0">Analyzing…</p>';
    try {
      renderAnalyze(await api("/api/table/" + encodeURIComponent(name) + "/analyze"));
    } catch (e) {
      inspectResult.innerHTML = emptyCard(e.message, e.hint);
    }
  };

  const commitMessageEl = document.getElementById("commit-message");
  const commitMessageCountEl = document.getElementById("commit-message-count");
  commitMessageEl.addEventListener("input", () => { commitMessageCountEl.textContent = commitMessageEl.value.length; });

  document.getElementById("btn-commit").onclick = async () => {
    const message = val("commit-message") || `from web UI: ${name}`;
    const setAsGolden = chk("commit-golden");
    try {
      const r = await api("/api/commit", { body: { message, only: [name] } });
      if (!r.committed.length) {
        toast("Nothing to save", "ok");
        return;
      }
      const snapshotId = r.committed[0].snapshot_id;
      const missing = r.committed[0].missing_outputs || [];
      if (setAsGolden) {
        await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
      }
      if (missing.length) {
        toast(
          `Snapshot #${snapshotId} saved, but the pipeline is incomplete${setAsGolden ? " (★ golden)" : ""}`,
          "warn",
          `Missing: ${missing.join(", ")} — a writer in the group produced no output`
        );
      } else {
        toast(`Snapshot #${snapshotId} saved${setAsGolden ? " (★ golden)" : ""}`, "ok");
      }
      commitMessageEl.value = "";
      commitMessageCountEl.textContent = "0";
      document.getElementById("commit-golden").checked = false;
      loadHistory(name);
      loadGoldenSummary(name);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("btn-rename-table").onclick = async () => {
    const newName = await promptDialog(`Rename '${name}' to:`, { value: name, confirmLabel: "Rename" });
    if (!newName || newName === name) return;
    try {
      const r = await api("/api/table/rename", { body: { table_name: name, new_name: newName } });
      invalidateTableSources(); // the name -> path map just changed
      toast(`Renamed to '${r.to}'`, "ok");
      location.hash = "#/table/" + encodeURIComponent(r.to);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("btn-clone-table").onclick = async () => {
    const newName = await promptDialog(`Clone '${name}' as:`, { value: name + "_copy", confirmLabel: "Clone" });
    if (!newName) return;
    try {
      const r = await api("/api/table/clone", { body: { table_name: name, new_name: newName } });
      invalidateTableSources(); // the name -> path map just changed
      toast(`Cloned as '${r.to}'`, "ok");
      location.hash = "#/table/" + encodeURIComponent(r.to);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("btn-delete-table").onclick = async () => {
    try {
      const preview = await api("/api/table/delete", { body: { table_name: name } });
      const lines = [`The following will be deleted: ${preview.sources.join(", ")} and its output.`];
      lines.push("History stays intact and browsable in 'History' — you can restore it from the Dashboard.");
      if (preview.is_batch) lines.push("Its [[batch_table]] will also be removed from table-tool.toml.");
      if (preview.dirty) lines.push("⚠ This table has unsaved changes: they will be lost forever.");
      const ok = await confirmDialog(lines.join(" "), { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      await api("/api/table/delete", { body: { table_name: name, confirm: true } });
      invalidateTableSources();
      toast(`'${name}' deleted (source + output)`, "ok");
      location.hash = "#/";
    } catch (e) {
      toastError(e);
    }
  };

  // the batch/single decision must be known BEFORE the loaders run:
  // loadSource/loadSidecarCard check isBatchTable() to avoid calling
  // endpoints that reject batches — awaiting ensureTableSources first
  // removes the race (Promise.all used to start them in parallel).
  await ensureTableSources(name);
  await Promise.all([
    loadSource(name), loadPipelineBuilder(name),
    loadSidecarCard(name), loadHistory(name), loadGoldenSummary(name),
  ]);
}

const HEX_PAGE_SIZE = 256;

// paged hex dump of a table's source, rendered via /api/view slices:
// unlike the old one-div-per-8-bytes dump, a multi-megabyte source can
// never freeze the tab, because only one page is in the DOM at a time
async function renderPagedHex(el, name, offset) {
  await ensureTableSources();
  const sourcePath = findSourcePath(name);
  const ir = await api(`/api/view?source=${encodeURIComponent(sourcePath)}&offset=${offset}&limit=${HEX_PAGE_SIZE}`);
  const bytes = atob(ir.data_base64);
  const hex = (n) => "0x" + n.toString(16).padStart(4, "0").toUpperCase();
  // same layout as the Files binary viewer: a real table with an
  // explicit header row, so the columns stay aligned for every page
  const rows = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    const parts = Array.from(chunk).map((c) => c.charCodeAt(0).toString(16).padStart(2, "0").toUpperCase());
    const g1 = parts.slice(0, 8).join(" ");
    const g2 = parts.slice(8).join(" ");
    const ascii = Array.from(chunk).map((c) => (c.charCodeAt(0) >= 32 && c.charCodeAt(0) <= 126 ? c : ".")).join("");
    const comment = (ir.comments.find((c) => c.offset === offset + i) || {}).text || "";
    rows.push(`
      <tr>
        <td class="hex-offset">${hex(offset + i)}</td>
        <td class="hex-bytes"><span class="hex-group">${escapeHtml(g1)}</span><span class="hex-group">${escapeHtml(g2)}</span></td>
        <td class="hex-ascii">${escapeHtml(ascii)}</td>
        ${comment ? `<td class="hex-comment">${escapeHtml(comment)}</td>` : `<td class="hex-comment"></td>`}
      </tr>`);
  }
  const pageEnd = offset + bytes.length;
  el.innerHTML = render`
    <div class="fs-binary-toolbar">
      <span class="subtitle mono">${fmtBytes(ir.length)}</span>
      <span class="flex-1"></span>
      <span class="fs-pager">
        <button id="hex-prev" ${offset === 0 ? "disabled" : ""} title="Previous page">${icon("up")}</button>
        <span class="mono fs-offset-label" title="Visible byte range">${hex(offset)}–${hex(pageEnd)}</span>
        <button id="hex-next" ${ir.has_more ? "" : "disabled"} title="Next page">${icon("down")}</button>
      </span>
    </div>
    <div class="hex-table-wrap">
      <table class="hex-table">
        <thead><tr>
          <th class="hex-offset">Offset</th>
          <th class="hex-bytes-head">00 01 02 03 04 05 06 07 &nbsp;&nbsp; 08 09 0A 0B 0C 0D 0E 0F</th>
          <th class="hex-ascii">ASCII</th>
          <th class="hex-comment">Comment</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  const prevBtn = document.getElementById("hex-prev");
  const nextBtn = document.getElementById("hex-next");
  if (prevBtn) prevBtn.onclick = () => renderPagedHex(el, name, Math.max(0, offset - HEX_PAGE_SIZE));
  if (nextBtn) nextBtn.onclick = () => renderPagedHex(el, name, offset + HEX_PAGE_SIZE);
}

/* Source content editor directly on the page: text formats only (CSV,
 * raw text, C, ...) — a file that doesn't decode as UTF-8 (binary
 * blob) stays read-only, shown as hex like before, with a warning
 * instead of an editor that would corrupt it on first save. */
async function loadSource(name) {
  const el = document.getElementById("view-result");
  try {
    if (isBatchTable(name)) {
      // a batch has no single editable source: show its members
      const resp = await api("/api/batch");
      const b = (resp.batches || []).find((x) => x.name === name);
      const members = b ? b.sources : [];
      el.innerHTML = render`
        <p class="subtitle">Batch table — ${members.length} member file${members.length === 1 ? "" : "s"} (concatenation order). Members and overrides are managed from the Dashboard's Settings modal.</p>
        <ol class="batch-members-list">
          ${members.map((m, i) => `<li><span class="mono">${i + 1}.</span> <span class="mono">${escapeHtml(m)}</span></li>`).join("")}
        </ol>
      `;
      return;
    }
    const info = await api("/api/source/" + encodeURIComponent(name));
    const msg = info.truncated
      ? "The source is larger than 1 MB: the editor opens read-only (first 1 MB) and the whole file is browsable in the hex view."
      : (info.editable ? "" : `${info.reason} — not editable from here, browse it in the hex view.`);
    el.innerHTML = render`
      ${raw(msg ? `<div class="result-line">${statusPill("warn").__raw}<span>${msg}</span></div>` : "")}
      <div class="fs-action-group">
        <span class="fs-action-label">Source</span>
        ${raw(info.editable
          ? `<button class="primary" id="btn-source-edit">${iconSpan("edit")}Edit</button>`
          : info.truncated
            ? `<button class="primary" id="btn-source-edit">${iconSpan("edit")}Edit (read-only)</button>`
            : "")}
        <button id="btn-source-hex">${icon("box")}View hex</button>
        ${raw(info.editable ? `<button id="btn-source-validate">${iconSpan("check")}Validate</button>` : "")}
      </div>
      <div id="source-validate-result"></div>
      <p class="subtitle mono m-0 mt-8">${info.path}</p>
    `;

    const hexBtn = document.getElementById("btn-source-hex");
    if (hexBtn) hexBtn.onclick = () => openTableHexModal(name);

    const editBtn = document.getElementById("btn-source-edit");
    if (editBtn && info.editable) {
      editBtn.onclick = () => openTextEditorModal({
        title: name,
        subtitle: info.path,
        initialContent: info.content,
        guardId: "source:" + name,
        onSave: async (content) => {
          await api("/api/source/" + encodeURIComponent(name), { method: "PUT", body: { content } });
          runValidate();
        },
      });
    } else if (editBtn) {
      // truncated (>1MB): read-only modal with the capped text
      editBtn.onclick = () => openTextEditorModal({
        title: name,
        subtitle: info.path,
        initialContent: info.content,
        readOnly: true,
        guardId: "source:" + name,
        onSave: async () => {},
      });
    }

    const runValidate = async () => {
      const resultEl = document.getElementById("source-validate-result");
      if (!resultEl) return;
      try {
        const r = await api("/api/source/" + encodeURIComponent(name) + "/validate", { method: "POST" });
        if (r.conforms) {
          resultEl.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforms to reader '${r.reader}'</span></div>`;
        } else {
          const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
          resultEl.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>doesn't conform to reader '${r.reader}'</span><ul>${items}</ul></div>`;
        }
      } catch (e) {
        toastError(e);
      }
    };
    const validateBtn = document.getElementById("btn-source-validate");
    if (validateBtn) validateBtn.onclick = runValidate;
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

/* The source's content as a near-fullscreen paged hex view. */
function openTableHexModal(name) {
  openDialog({
    large: true,
    title: name,
    body: render`<div id="source-hex"></div>`,
  });
  renderPagedHex(document.getElementById("source-hex"), name, 0);
}

function _stageToRawJs(stage) {
  if (stage.type === "reader" || stage.type === "writer") return { type: stage.type, name: stage.name };
  const out = { type: "exec", command: stage.command, on_error: stage.on_error || "fail" };
  if (stage.output_extension) out.output_extension = stage.output_extension;
  return out;
}

/* Visual pipeline builder: shows the current resolution (implicit from
 * --from/--to, or explicit from sidecar) as an editable stage list —
 * reordering/adding/removing client-side, but NO alternation rule
 * duplicated here: 'Save' sends the raw list to PUT
 * /api/pipeline/{table}, which validates with the same core
 * PipelineSpec.from_raw_stages() and responds with 'stage_index' on
 * error — that's what highlights the offending card. */
async function loadPipelineBuilder(name) {
  const el = document.getElementById("pipeline-result");
  try {
    const p = await api("/api/pipeline/" + encodeURIComponent(name));
    el.innerHTML = render`
      <p class="subtitle m-0">${p.explicit
        ? `Explicit pipeline saved in the sidecar — ${p.stages.length} stage${p.stages.length === 1 ? "" : "s"}.`
        : `Automatic resolution: ${p.stages.map((st) => st.kind === "exec" ? "exec" : st.name).join(" → ")}.`}</p>
      <div class="pipeline-actions mt-10">
        <button class="primary" id="pb-edit-graph">${icon("box")}Edit graph</button>
        ${raw(p.explicit ? `<button id="pb-reset">${iconSpan("refresh")}Restore implicit</button>` : "")}
      </div>
    `;
    document.getElementById("pb-edit-graph").onclick = () => openPipelineEditor(name);
    const resetBtn = document.getElementById("pb-reset");
    if (resetBtn) {
      resetBtn.onclick = async () => {
        const ok = await confirmDialog("Go back to automatic resolution from --from/--to? The explicit pipeline saved in the sidecar will be removed.", { danger: true, confirmLabel: "Restore" });
        if (!ok) return;
        try {
          await api("/api/pipeline/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Pipeline restored to automatic resolution", "ok");
          loadPipelineBuilder(name);
        } catch (e) {
          toastError(e);
        }
      };
    }
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

/* Sidecar card: one switch per field (schema shared with the global
 * config, see /api/config 'schema') — only fields with the switch on
 * end up in the PUT, consistent with core/config.py's "the sidecar
 * only overrides the keys it declares" model. */
async function loadSidecarCard(name) {
  const el = document.getElementById("sidecar-result");
  try {
    if (isBatchTable(name)) {
      // a batch has no sidecar: its overrides live inline in
      // [[batch_table]] (read-only here — edit from the Dashboard)
      const resp = await api("/api/batch");
      const b = (resp.batches || []).find((x) => x.name === name);
      const chip = (label, value) => `<div class="field"><label>${label}</label><input type="text" class="mono" value="${escapeHtml(value || "")}" disabled></div>`;
      el.innerHTML = `
        <p class="subtitle">Batch overrides live inline in [[batch_table]] — managed from the Dashboard's Settings modal.</p>
        <div class="field-row">
          ${chip("Reader", b ? b.reader : "")}
          ${chip("Writer", b ? b.writer : "")}
          ${chip("Byte order", b ? b.byte_order : "")}
        </div>
      `;
      return;
    }
    const [sidecar, cfg] = await Promise.all([api("/api/sidecar/" + encodeURIComponent(name)), api("/api/config")]);
    const schema = cfg.schema;
    const sidecarDefaults = sidecar.defaults || {};

    function row(section, f, current) {
      const has = Object.prototype.hasOwnProperty.call(current, f.key);
      const id = `sc-${section}-${f.key}`;
      const value = has ? current[f.key] : undefined;
      let inputHtml;
      if (f.type === "list") {
        inputHtml = `<input type="text" id="${id}" ${has ? "" : "disabled"} value="${escapeHtml((value || []).join(", "))}" placeholder="comma-separated">`;
      } else if (f.key === "byte_order") {
        inputHtml = `<select id="${id}" ${has ? "" : "disabled"}><option value="little" ${value !== "big" ? "selected" : ""}>little</option><option value="big" ${value === "big" ? "selected" : ""}>big</option></select>`;
      } else {
        inputHtml = `<input type="text" id="${id}" ${has ? "" : "disabled"} value="${escapeHtml(value === undefined ? "" : value)}">`;
      }
      return `<div class="field-with-toggle"><input type="checkbox" id="${id}-toggle" ${has ? "checked" : ""} data-toggle-for="${id}"><div class="field"><label>${escapeHtml(f.key)}</label>${inputHtml}</div></div>`;
    }

    const defaultsRows = schema.defaults.map((f) => row("defaults", f, sidecarDefaults)).join("");
    const hasSidecar = Object.keys(sidecar).length > 0;

    el.innerHTML = `
      <p class="subtitle">Only the selected fields override the global config for this table.</p>
      <h2 class="mt-16">Defaults</h2>
      ${defaultsRows}
      <div class="toolbar mt-14">
        <button type="button" class="primary" id="sc-save">${iconSpan("save")}Save sidecar</button>
        <button type="button" class="danger" id="sc-delete" ${hasSidecar ? "" : "disabled"}>${iconSpan("trash")}Delete sidecar</button>
      </div>
    `;

    el.querySelectorAll("[data-toggle-for]").forEach((cb) => {
      cb.onchange = () => { document.getElementById(cb.getAttribute("data-toggle-for")).disabled = !cb.checked; };
    });

    document.getElementById("sc-save").onclick = async () => {
      const defaults = {};
      schema.defaults.forEach((f) => {
        const id = `sc-defaults-${f.key}`;
        if (document.getElementById(`${id}-toggle`).checked) defaults[f.key] = val(id) || null;
      });
      try {
        await api("/api/sidecar/" + encodeURIComponent(name), { method: "PUT", body: { defaults } });
        toast("Sidecar saved", "ok");
        loadSidecarCard(name);
      } catch (e) {
        toastError(e);
      }
    };

    document.getElementById("sc-delete").onclick = async () => {
      const ok = await confirmDialog(`Delete the specific config for '${name}'? It will go back to using only the global config.`, { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      try {
        await api("/api/sidecar/" + encodeURIComponent(name), { method: "DELETE" });
        toast("Sidecar deleted", "ok");
        loadSidecarCard(name);
      } catch (e) {
        toastError(e);
      }
    };
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

/* How THIS snapshot was built — reader/writer are inferred after the
 * fact from the files actually committed (accurate even with an
 * ad-hoc --to writer never written to config), not from "what the
 * config would resolve now". For an explicit pipeline we don't
 * summarize the stages inline (misleading/incomplete for a fan-out or
 * exec stage) — a badge with hover (native title, "hover type") shows
 * the full sequence only when it's really needed. */
function _snapshotBuildInfoHtml(s) {
  if (s.pipeline_explicit) {
    const detail = s.pipeline_description || "explicit pipeline";
    return `<span class="pill pill-dim snapshot-pipeline-badge" title="${escapeHtml(detail)}">${iconSpan("box")}explicit pipeline</span>`;
  }
  const bits = [s.reader, (s.writers && s.writers.length) ? s.writers.join(" + ") : null].filter(Boolean);
  return bits.length ? `<div class="snapshot-build-info mono">${escapeHtml(bits.join(" → "))}</div>` : "";
}

/* Golden summary above the commit form: 4-value status (match/
 * mismatch/stale/missing) — 'stale' is the new case, the source
 * changed after golden so the output comparison is no longer
 * reliable, distinct from a real mismatch. */
async function loadGoldenSummary(name) {
  const el = document.getElementById("golden-summary");
  try {
    const g = await api("/api/golden/" + encodeURIComponent(name));
    el.innerHTML = render`
      <div class="result-line">
        <strong>Golden:</strong>
        ${statusPill(g.status)}
        ${raw(g.golden_snapshot_id ? `<span class="subtitle">snapshot #${g.golden_snapshot_id}</span>` : "")}
        ${raw(g.golden_snapshot_id ? '<button class="ghost" id="btn-golden-clear">Remove</button>' : "")}
      </div>
    `;
    const clearBtn = document.getElementById("btn-golden-clear");
    if (clearBtn) {
      clearBtn.onclick = async () => {
        const ok = await confirmDialog(`Remove the golden reference for '${name}'? Snapshots stay, only the pointer is removed.`, { danger: true, confirmLabel: "Remove" });
        if (!ok) return;
        try {
          await api("/api/golden/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Golden removed", "ok");
          loadGoldenSummary(name);
          loadHistory(name);
        } catch (e) {
          toastError(e);
        }
      };
    }
  } catch (e) {
    el.innerHTML = "";
  }
}

function _snapshotItemHtml(s, goldenId, headId) {
  const isGolden = s.id === goldenId;
  const isCurrent = s.id === headId;
  const isIncomplete = s.missing_outputs && s.missing_outputs.length > 0;
  const classes = ["snapshot-item", "snapshot-row", isGolden ? "is-golden" : "", isCurrent ? "is-current" : "", isIncomplete ? "is-incomplete" : ""]
    .filter(Boolean).join(" ");
  return render`
    <button type="button" class="${classes}" data-snapshot="${s.id}" title="Click for details">
      <span class="snapshot-id mono">#${s.id}</span>
      ${raw(isCurrent ? `<span class="pill pill-current">● current</span>` : isGolden ? goldBadge() : "")}
      <span class="snapshot-row-msg">${s.message}</span>
      <span class="snapshot-row-time mono">${s.timestamp.slice(0, 16)}</span>
    </button>`;
}

/* Clicking a snapshot opens a modal with its full information (build
 * info, outputs, warnings) and the per-snapshot actions — the list
 * itself stays compact. */
function _openSnapshotModal(s, name, goldenId, headId) {
  const isGolden = s.id === goldenId;
  const isCurrent = s.id === headId;
  const isIncomplete = s.missing_outputs && s.missing_outputs.length > 0;
  const body = render`
    <div class="snapshot-modal">
      <div class="result-line">
        ${raw((isCurrent ? currentBadge() : "") + (isGolden ? goldBadge() : ""))}
        <span class="subtitle mono">${s.timestamp}</span>
      </div>
      <p class="snapshot-modal-msg">${s.message}</p>
      ${raw(_snapshotBuildInfoHtml(s))}
      <p class="subtitle">Outputs</p>
      <div class="mono snapshot-modal-outputs">${s.outputs.length ? s.outputs.join(", ") : "—"}</div>
      ${raw(isIncomplete
        ? `<div class="snapshot-warning">${iconSpan("warnTri")}incomplete pipeline — missing ${escapeHtml(s.missing_outputs.join(", "))}</div>`
        : "")}
    </div>
  `;
  const downloadUrl = `/api/log/${encodeURIComponent(name)}/${s.id}/download`;
  const actions = [
    { label: "Download", value: "download", autofocus: true },
  ];
  if (!isGolden) actions.push({ label: "Set as golden", value: "golden" });
  if (!isCurrent) actions.push({ label: "Restore", danger: true, value: "restore" });
  // the window-style X doubles as Close (cancelValue === "close")
  openDialog({ title: `Snapshot #${s.id}`, body, actions, cancelValue: "close" }).then((action) => {
    if (action === "restore") restoreSnapshotFlow(name, s.id);
    else if (action === "golden") goldenSnapshotFlow(name, s.id);
    else if (action === "download") window.location.href = downloadUrl;
  });
}

async function restoreSnapshotFlow(name, snapshotId) {
  try {
    const preview = await api("/api/restore", { body: { table_name: name, snapshot_id: snapshotId } });
    const previewLabel = preview.source || preview.sources.join(", ");
    const ok = await confirmDialog(`Overwrite ${previewLabel} with the state of snapshot #${snapshotId}?`, { danger: true, confirmLabel: "Overwrite" });
    if (!ok) return;
    const r = await api("/api/restore", { body: { table_name: name, snapshot_id: snapshotId, confirm: true } });
    const removedNote = r.removed.length ? ` — removed (weren't part of the snapshot): ${r.removed.join(", ")}` : "";
    toast(`Restored: ${r.written.join(", ")}${removedNote}`, "ok");
    loadSource(name);
    loadPipelineBuilder(name);
    loadHistory(name);
    loadGoldenSummary(name);
  } catch (e) {
    toastError(e);
  }
}

async function goldenSnapshotFlow(name, snapshotId) {
  try {
    await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
    toast(`Golden set to snapshot #${snapshotId}`, "ok");
    loadHistory(name);
    loadGoldenSummary(name);
  } catch (e) {
    toastError(e);
  }
}

function _loadMoreButtonHtml(offset, remaining) {
  return `<button class="ghost" id="btn-load-more-snapshots" data-offset="${offset}">${iconSpan("down")}Load more (${remaining} remaining)</button>`;
}

/* History is loaded in pages (see HISTORY_PAGE_SIZE): with many
 * snapshots a single table would make the page very long and
 * unbalanced compared to the main column (History is docked to the
 * side, see .table-detail-side). A single delegated listener on
 * #history-result handles restore/golden/load-more: adding further
 * pages doesn't require re-attaching handlers to the new nodes. */
async function loadHistory(name) {
  const el = document.getElementById("history-result");
  try {
    const [log, golden] = await Promise.all([
      api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=0`),
      api("/api/golden/" + encodeURIComponent(name)),
    ]);
    if (!log.snapshots.length) {
      el.innerHTML = '<p class="empty-state">No snapshot yet.</p>';
      return;
    }
    const goldenId = golden.golden_snapshot_id;
    const items = log.snapshots.map((s) => _snapshotItemHtml(s, goldenId, log.head_snapshot_id));
    const known = [...log.snapshots]; // accumulates load-more pages too
    el.innerHTML = render`
      <div class="snapshot-list" id="snapshot-list">${items}</div>
      ${raw(log.has_more ? _loadMoreButtonHtml(log.snapshots.length, log.total - log.snapshots.length) : "")}
    `;

    el.onclick = async (event) => {
      // clicking a snapshot opens its detail modal (info + actions)
      const row = event.target.closest("[data-snapshot]");
      if (row) {
        const snap = known.find((s) => s.id === Number(row.dataset.snapshot));
        if (snap) _openSnapshotModal(snap, name, goldenId, log.head_snapshot_id);
        return;
      }

      const loadMoreBtn = event.target.closest("#btn-load-more-snapshots");
      if (loadMoreBtn) {
        const offset = Number(loadMoreBtn.getAttribute("data-offset"));
        loadMoreBtn.disabled = true;
        try {
          const next = await api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`);
          const list = document.getElementById("snapshot-list");
          const nextHtml = next.snapshots.map((s) => _snapshotItemHtml(s, goldenId, next.head_snapshot_id)).join("");
          list.insertAdjacentHTML("beforeend", nextHtml);
          known.push(...next.snapshots);
          const newOffset = offset + next.snapshots.length;
          if (next.has_more) {
            loadMoreBtn.outerHTML = _loadMoreButtonHtml(newOffset, next.total - newOffset);
          } else {
            loadMoreBtn.remove();
          }
        } catch (e) {
          toastError(e);
          loadMoreBtn.disabled = false;
        }
      }
    };

    // right-click on a snapshot: quick actions without opening the modal
    el.addEventListener("contextmenu", (event) => {
      const row = event.target.closest("[data-snapshot]");
      if (!row) return;
      event.preventDefault();
      const snapId = Number(row.dataset.snapshot);
      const isGolden = row.classList.contains("is-golden");
      const isCurrent = row.classList.contains("is-current");
      const items = [
        {
          label: "Details",
          icon: "edit",
          action: () => {
            const snap = known.find((s) => s.id === snapId);
            if (snap) _openSnapshotModal(snap, name, goldenId, log.head_snapshot_id);
          },
        },
        {
          label: "Download",
          icon: "download",
          action: () => { window.location.href = `/api/log/${encodeURIComponent(name)}/${snapId}/download`; },
        },
      ];
      if (!isGolden) items.push({ label: "Set as golden", icon: "star", action: () => goldenSnapshotFlow(name, snapId) });
      if (!isCurrent) items.push({ label: "Restore", icon: "refresh", danger: true, action: () => restoreSnapshotFlow(name, snapId) });
      openContextMenu(items, event.clientX, event.clientY);
    });
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

export { viewTable };
