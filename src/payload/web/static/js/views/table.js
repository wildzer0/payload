/* Table detail view (route "/table/<name>"): build form, source
 * content editor, visual pipeline builder, sidecar card, tags &
 * cluster, history (commit/log/restore/golden). Split out of the
 * former single-file app.js — no behavior change. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, ICONS, toast, toastError,
  confirmDialog, statusPill, pageHeader, detailsCard, pinnedCard,
  goldBadge, currentBadge, baseName, val, chk, attachAutocomplete,
} from "../ui.js";
import { api, getPlugins, ensureTableSources, findSourcePath } from "../api.js";

const COMMIT_MESSAGE_MAX_LENGTH = 1024;
const HISTORY_PAGE_SIZE = 4;

async function viewTable(name) {
  const content = document.getElementById("content");

  const buildBody = `
    <div class="field-row">
      <div class="field"><label>Writer (--to)</label><div class="autocomplete-wrap"><input type="text" id="f-to" placeholder="bin"></div></div>
      <div class="field"><label>Reader (--from)</label><div class="autocomplete-wrap"><input type="text" id="f-from" placeholder="auto"></div></div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="f-force"><span>--force</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-dry"><span>--dry-run</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-golden"><span>--check-golden</span></label>
    </div>
    <div class="build-actions">
      <button class="primary" id="btn-build">${iconSpan("play")}Build</button>
    </div>
    <div id="build-result"></div>
  `;

  const historyBody = `
    <div id="golden-summary" style="margin-bottom:14px"></div>
    <div class="field">
      <label>Commit message</label>
      <textarea id="commit-message" class="commit-message-input mono" rows="3" maxlength="${COMMIT_MESSAGE_MAX_LENGTH}" placeholder="Describe what changed…"></textarea>
      <div class="field-hint"><span id="commit-message-count">0</span>/${COMMIT_MESSAGE_MAX_LENGTH}</div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="commit-golden"><span>${iconSpan("star")}Also set as golden</span></label>
    </div>
    <div class="build-actions">
      <button id="btn-commit">${iconSpan("save")}Commit changes</button>
    </div>
    <div id="history-result"></div>
  `;

  const headerActionsHtml = `
    <a class="btn icon-only" id="btn-download-output" href="/api/table/${encodeURIComponent(name)}/download" title="Download the last built output" download hidden>${iconSpan("download")}</a>
    <button class="danger" id="btn-delete-table">${iconSpan("trash")}Delete table</button>
  `;

  const tagsClusterBody = `
    <div class="field">
      <label>Cluster</label>
      <select id="tc-cluster" class="inline-select" style="width:100%;max-width:none"><option value="">— none —</option></select>
    </div>
    <div class="field">
      <label>Tags</label>
      <div id="tc-tag-chips" class="table-summary-tags"></div>
      <input type="text" id="tc-tag-input" placeholder="Add a tag, press Enter" style="margin-top:8px">
    </div>
  `;

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/">← Dashboard</a></div>
    ${raw(pageHeader(name, undefined, headerActionsHtml))}
    <div class="table-detail-layout">
      <div class="table-detail-main">
        ${raw(detailsCard("Build", buildBody, { open: true }))}
        ${raw(detailsCard("Source content", '<div id="view-result"><p class="empty-state">—</p></div>', { open: false }))}
        ${raw(detailsCard("Pipeline", '<div id="pipeline-result"></div>', { open: true }))}
        ${raw(detailsCard("Table-specific config (sidecar)", '<div id="sidecar-result"></div>', { open: false }))}
      </div>
      <div class="table-detail-side">
        ${raw(pinnedCard("Tags & cluster", tagsClusterBody))}
        ${raw(pinnedCard("History", historyBody))}
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
    document.getElementById("tc-tag-chips").innerHTML = chips || '<span class="empty-state" style="margin:0">No tag</span>';
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

  Promise.all([api("/api/clusters"), api("/api/report")]).then(([clustersResp, report]) => {
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

    if (row.output_size != null) document.getElementById("btn-download-output").hidden = false;
    if (row.pipeline_explicit) return;
    if (row.resolved_reader) document.getElementById("f-from").placeholder = row.resolved_reader;
    if (row.resolved_writer) document.getElementById("f-to").placeholder = row.resolved_writer;
  }).catch(() => {});

  document.getElementById("btn-build").onclick = async () => {
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
      toast(`'${name}' deleted (source + output)`, "ok");
      location.hash = "#/";
    } catch (e) {
      toastError(e);
    }
  };

  await Promise.all([
    ensureTableSources(), loadSource(name), loadPipelineBuilder(name),
    loadSidecarCard(name), loadHistory(name), loadGoldenSummary(name),
  ]);
}

async function hexDumpHtml(name) {
  await ensureTableSources();
  const ir = await api("/api/view?source=" + encodeURIComponent(findSourcePath(name)));
  const bytes = atob(ir.data_base64);
  const hexLines = [];
  for (let i = 0; i < bytes.length; i += 8) {
    const chunk = bytes.slice(i, i + 8);
    const hex = Array.from(chunk).map((c) => c.charCodeAt(0).toString(16).padStart(2, "0").toUpperCase()).join(" ");
    const comment = (ir.comments.find((c) => c.offset === i) || {}).text || "";
    hexLines.push(render`<div class="hex-chunk"><span class="offset">0x${i.toString(16).padStart(4, "0").toUpperCase()}</span><span>${hex}</span><span style="color:var(--text-dim)">${comment}</span></div>`);
  }
  return `<div class="log">${hexLines.join("") || '<span class="log-empty">empty</span>'}</div>`;
}

/* Source content editor directly on the page: text formats only (CSV,
 * raw text, C, ...) — a file that doesn't decode as UTF-8 (binary
 * blob) stays read-only, shown as hex like before, with a warning
 * instead of an editor that would corrupt it on first save. */
async function loadSource(name) {
  const el = document.getElementById("view-result");
  try {
    const info = await api("/api/source/" + encodeURIComponent(name));
    if (info.editable) {
      el.innerHTML = render`
        <textarea id="source-editor" class="source-editor mono" spellcheck="false" rows="14">${info.content}</textarea>
        <div class="source-editor-actions">
          <button class="primary" id="btn-save-source">${icon("save")}Save source</button>
          <button id="btn-validate-source">${icon("check")}Validate with default reader</button>
          <span class="subtitle mono">${info.path}</span>
        </div>
        <div id="source-validate-result"></div>
      `;
      const runValidate = async () => {
        const resultEl = document.getElementById("source-validate-result");
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
      document.getElementById("btn-save-source").onclick = async () => {
        try {
          await api("/api/source/" + encodeURIComponent(name), { method: "PUT", body: { content: document.getElementById("source-editor").value } });
          toast("Source saved", "ok");
          runValidate();
        } catch (e) {
          toastError(e);
        }
      };
      document.getElementById("btn-validate-source").onclick = runValidate;
    } else {
      const hex = await hexDumpHtml(name);
      el.innerHTML = render`
        <div class="result-line">${statusPill("warn")}<span>${info.reason} — not editable from here, hex view only.</span></div>
        ${raw(hex)}
      `;
    }
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
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
    const [p, plugins] = await Promise.all([api("/api/pipeline/" + encodeURIComponent(name)), getPlugins()]);
    const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
    const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);

    const stages = p.stages.map((s) => (
      s.kind === "exec"
        ? { type: "exec", command: s.command, on_error: s.on_error, output_extension: "" }
        : { type: s.kind, name: s.name }
    ));
    let lastError = null;

    function optionList(names, selected) {
      return names.map((n) => `<option value="${escapeHtml(n)}" ${n === selected ? "selected" : ""}>${escapeHtml(n)}</option>`).join("");
    }

    function renderStages() {
      const cards = stages.map((s, i) => {
        const badgeCls = s.type === "reader" ? "badge-reader" : s.type === "writer" ? "badge-writer" : "";
        const hasError = lastError && lastError.stage_index === i;
        let fields;
        if (s.type === "reader" || s.type === "writer") {
          const names = s.type === "reader" ? readerNames : writerNames;
          fields = `<select data-field="name" data-idx="${i}">${optionList(names, s.name)}</select>`;
        } else {
          fields = `
            <input type="text" data-field="command" data-idx="${i}" value="${escapeHtml(s.command || "")}" placeholder="external command, e.g. objcopy {input} {output}" style="flex:1;min-width:220px">
            <select data-field="on_error" data-idx="${i}">
              <option value="fail" ${s.on_error !== "warn" ? "selected" : ""}>on_error: fail</option>
              <option value="warn" ${s.on_error === "warn" ? "selected" : ""}>on_error: warn</option>
            </select>
            <input type="text" data-field="output_extension" data-idx="${i}" value="${escapeHtml(s.output_extension || "")}" placeholder="extension if final, e.g. .signed.bin" style="width:200px">
          `;
        }
        return `
          <div class="stage-card ${hasError ? "stage-error" : ""}">
            <span class="stage-badge ${badgeCls}">${s.type}</span>
            <div class="stage-fields">${fields}</div>
            <div class="stage-actions">
              <button type="button" class="icon-only ghost" data-move="up" data-idx="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">${iconSpan("up")}</button>
              <button type="button" class="icon-only ghost" data-move="down" data-idx="${i}" ${i === stages.length - 1 ? "disabled" : ""} aria-label="Move down">${iconSpan("down")}</button>
              <button type="button" class="icon-only ghost danger" data-remove="${i}" aria-label="Remove">${iconSpan("trash")}</button>
            </div>
          </div>
          ${hasError ? `<div class="stage-error-msg">${escapeHtml(lastError.message)}</div>` : ""}
        `;
      }).join("");

      el.innerHTML = `
        <div class="stage-list">${cards || '<p class="empty-state">No stage — add one to get started.</p>'}</div>
        <div class="add-stage-row">
          <select id="pb-add-type">
            <option value="reader">reader</option>
            <option value="writer">writer</option>
            <option value="exec">exec</option>
          </select>
          <button type="button" id="pb-add"><span class="icon">${ICONS.plus}</span>Add stage</button>
          <span style="flex:1"></span>
          <button type="button" id="pb-reset">${iconSpan("refresh")}Restore implicit</button>
          <button type="button" class="primary" id="pb-save">${iconSpan("save")}Save pipeline</button>
        </div>
        <div class="pipeline-output-row">
          <span class="pipeline-output-label">Output</span>
          ${p.outputs.length
            ? p.outputs.map((o) => `<span class="pill pill-dim mono" title="${escapeHtml(o)}">${escapeHtml(baseName(o))}</span>`).join("")
            : '<span class="subtitle">—</span>'}
          ${p.explicit ? '<span class="pill pill-warn">explicit (sidecar)</span>' : '<span class="pill pill-dim">automatic</span>'}
        </div>
      `;

      // Every local change invalidates the last save attempt's error:
      // both because stage indices might have changed (the highlight
      // would land on the wrong stage), and because the user may have
      // already fixed the problem — the error signal must disappear
      // right away, not stay stuck until Save is pressed again.
      el.querySelectorAll("[data-move]").forEach((btn) => {
        btn.onclick = () => {
          const i = Number(btn.getAttribute("data-idx"));
          const j = i + (btn.getAttribute("data-move") === "up" ? -1 : 1);
          if (j < 0 || j >= stages.length) return;
          const tmp = stages[i]; stages[i] = stages[j]; stages[j] = tmp;
          lastError = null;
          renderStages();
        };
      });
      el.querySelectorAll("[data-remove]").forEach((btn) => {
        btn.onclick = () => {
          stages.splice(Number(btn.getAttribute("data-remove")), 1);
          lastError = null;
          renderStages();
        };
      });
      el.querySelectorAll("[data-field]").forEach((input) => {
        input.onchange = () => {
          stages[Number(input.getAttribute("data-idx"))][input.getAttribute("data-field")] = input.value;
          lastError = null;
          renderStages();
        };
      });

      document.getElementById("pb-add").onclick = () => {
        const type = document.getElementById("pb-add-type").value;
        if (type === "exec") {
          stages.push({ type: "exec", command: "", on_error: "fail", output_extension: "" });
        } else {
          const names = type === "reader" ? readerNames : writerNames;
          stages.push({ type, name: names[0] || "" });
        }
        lastError = null;
        renderStages();
      };

      document.getElementById("pb-save").onclick = async () => {
        try {
          await api("/api/pipeline/" + encodeURIComponent(name), { method: "PUT", body: { stages: stages.map(_stageToRawJs) } });
          toast("Pipeline saved", "ok");
          loadPipelineBuilder(name);
        } catch (e) {
          lastError = { stage_index: e.data && typeof e.data.stage_index === "number" ? e.data.stage_index : -1, message: e.message };
          renderStages();
          toastError(e);
        }
      };

      document.getElementById("pb-reset").onclick = async () => {
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

    renderStages();
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
      <h2 style="margin-top:16px">Defaults</h2>
      ${defaultsRows}
      <div class="toolbar" style="margin-top:14px">
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

function _snapshotItemHtml(s, name, goldenId, headId) {
  const isGolden = s.id === goldenId;
  const isCurrent = s.id === headId;
  const isIncomplete = s.missing_outputs && s.missing_outputs.length > 0;
  const itemClasses = ["snapshot-item", isGolden ? "is-golden" : "", isCurrent ? "is-current" : "", isIncomplete ? "is-incomplete" : ""]
    .filter(Boolean).join(" ");
  return render`
    <div class="${itemClasses}">
      <div class="snapshot-item-head">
        <span class="snapshot-id mono">#${s.id}</span>
        ${raw((isCurrent ? currentBadge() : "") + (isGolden ? goldBadge() : ""))}
      </div>
      <div class="snapshot-item-meta mono">${s.timestamp}</div>
      <div class="snapshot-item-msg">${s.message}</div>
      ${raw(_snapshotBuildInfoHtml(s))}
      <div class="snapshot-item-outputs mono">${s.outputs.length ? s.outputs.join(", ") : "—"}</div>
      ${raw(isIncomplete
        ? `<div class="snapshot-warning">${iconSpan("warnTri")}incomplete pipeline — missing ${escapeHtml(s.missing_outputs.join(", "))}</div>`
        : "")}
      <div class="table-actions">
        ${raw(isCurrent
          ? `<button disabled title="Already the current snapshot">Restore</button>`
          : `<button data-restore="${s.id}">Restore</button>`)}
        ${raw(isGolden ? "" : `<button class="ghost" data-set-golden="${s.id}">${iconSpan("star")}Golden</button>`)}
        <a class="btn" href="/api/log/${encodeURIComponent(name)}/${s.id}/download" download>${icon("save")}Download</a>
      </div>
    </div>`;
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
    const items = log.snapshots.map((s) => _snapshotItemHtml(s, name, goldenId, log.head_snapshot_id));
    el.innerHTML = render`
      <div class="snapshot-list" id="snapshot-list">${items}</div>
      ${raw(log.has_more ? _loadMoreButtonHtml(log.snapshots.length, log.total - log.snapshots.length) : "")}
    `;

    el.onclick = async (event) => {
      const restoreBtn = event.target.closest("[data-restore]");
      if (restoreBtn) {
        const snapshotId = Number(restoreBtn.getAttribute("data-restore"));
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
        return;
      }

      const goldenBtn = event.target.closest("[data-set-golden]");
      if (goldenBtn) {
        const snapshotId = Number(goldenBtn.getAttribute("data-set-golden"));
        try {
          await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
          toast(`Golden set to snapshot #${snapshotId}`, "ok");
          loadHistory(name);
          loadGoldenSummary(name);
        } catch (e) {
          toastError(e);
        }
        return;
      }

      const loadMoreBtn = event.target.closest("#btn-load-more-snapshots");
      if (loadMoreBtn) {
        const offset = Number(loadMoreBtn.getAttribute("data-offset"));
        loadMoreBtn.disabled = true;
        try {
          const next = await api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`);
          const list = document.getElementById("snapshot-list");
          const nextHtml = next.snapshots.map((s) => _snapshotItemHtml(s, name, goldenId, next.head_snapshot_id)).join("");
          list.insertAdjacentHTML("beforeend", nextHtml);
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
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

export { viewTable };
