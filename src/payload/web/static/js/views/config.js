/* Settings modal: the global table-tool.toml form (defaults + plugin
 * options, dirty tracking, TOML preview, per-field origin pills) plus
 * the detailed resolution table. Opened from the sidebar gear — the
 * former "/config" page became a modal. Closing with unsaved changes
 * asks for confirmation. */
"use strict";

import {
  escapeHtml, raw, render, statusPill, icon, val, toast, toastError,
  registerDirtyGuard, removeDirtyGuard, confirmDialog,
} from "../ui.js";
import { api } from "../api.js";

function _cfgFieldId(section, key) { return `cfg-${section}-${key}`; }

// Labels/descriptions hand-written client-side: the schema coming from
// /api/config only carries key and type (see config_schema() in
// core/config.py), not help text — the backend shouldn't need to know
// how the webapp prefers to explain each field.
const CONFIG_FIELD_META = {
  "defaults.reader": {
    label: "Default reader",
    desc: "Used when --from isn't specified and it can't be inferred from the file extension. Empty = auto-resolution from extension/sniff.",
  },
  "defaults.writer": {
    label: "Default writer",
    desc: "Used when --to isn't specified. Empty = no explicit preference, the reader may suggest one.",
  },
  "defaults.output_dir": {
    label: "Output folder",
    desc: "Where built files end up, relative to the project root.",
  },
  "defaults.cache_dir": {
    label: "Cache folder",
    desc: "Where payload keeps build checkpoints — avoids rebuilding when source and config haven't changed.",
  },
  "defaults.byte_order": {
    label: "Byte order",
    desc: "Default endianness for readers/writers that handle multi-byte values.",
  },
};

function _cfgFieldMarkup(section, f, currentByKey, originByKey) {
  const key = `${section}.${f.key}`;
  const value = currentByKey[key];
  const id = _cfgFieldId(section, f.key);
  const meta = CONFIG_FIELD_META[key] || { label: f.key, desc: "" };
  const origin = originByKey[key] || "default";
  // "default" = never customized, using the tool's factory value.
  // "customized" = present in table-tool.toml — has NOTHING to do
  // with any unsaved changes in the form (that's
  // "settings-row-dirty-note" below, deliberately kept separate: they
  // are two different pieces of information, "what's saved" versus
  // "what you're writing right now").
  const originPill = origin === "default"
    ? raw('<span class="pill pill-dim">default</span>')
    : raw('<span class="pill pill-current">customized</span>');

  let control;
  if (f.type === "list") {
    control = render`<input type="text" id="${id}" data-cfg-field="${key}" value="${(value || []).join(", ")}" placeholder="comma-separated">`;
  } else if (f.key === "byte_order") {
    control = `<select id="${id}" data-cfg-field="${key}"><option value="little" ${value !== "big" ? "selected" : ""}>little</option><option value="big" ${value === "big" ? "selected" : ""}>big</option></select>`;
  } else {
    const shown = value === undefined || value === null ? "" : value;
    control = render`<input type="text" id="${id}" data-cfg-field="${key}" value="${shown}" placeholder="${f.key === "writer" || f.key === "reader" ? "no preference" : "(default)"}">`;
  }

  return render`
    <div class="settings-row" data-row-key="${key}">
      <div class="settings-row-info">
        <div class="settings-row-label">${meta.label}${originPill}</div>
        ${raw(meta.desc ? render`<div class="settings-row-desc">${meta.desc}</div>` : "")}
      </div>
      <div class="settings-row-control">
        ${raw(control)}
        <span class="settings-row-dirty-note" hidden>● unsaved change</span>
      </div>
    </div>`;
}

function _cfgReadFormValues(schema) {
  const defaults = {};
  for (const f of schema.defaults) defaults[f.key] = val(_cfgFieldId("defaults", f.key)) || null;
  return { defaults };
}

// Lightweight preview of the TOML that would be written — not a full
// TOML serializer (no need: only defaults, values are always
// string/list/null), just enough to show the user what's about to be
// saved before they press "Save".
function _cfgTomlPreview(values) {
  const fmtVal = (v) => {
    if (Array.isArray(v)) return v.length ? `[${v.map((x) => JSON.stringify(x)).join(", ")}]` : null;
    if (v === null || v === undefined || v === "") return null;
    return JSON.stringify(String(v));
  };
  const section = (name, obj) => {
    const lines = Object.entries(obj).map(([k, v]) => [k, fmtVal(v)]).filter(([, v]) => v !== null).map(([k, v]) => `${k} = ${v}`);
    return `[${name}]` + (lines.length ? `\n${lines.join("\n")}` : "\n# (no override, all defaults)");
  };
  return section("defaults", values.defaults);
}

async function openSettingsModal() {
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  box.classList.add("modal-large");

  const build = async () => {
    const r = await api("/api/config");
    const schema = r.schema;
    const currentByKey = Object.fromEntries(r.fields.map((f) => [f.key, f.value]));
    const originByKey = Object.fromEntries(r.fields.map((f) => [f.key, f.origin]));

    const defaultsRows = schema.defaults.map((f) => _cfgFieldMarkup("defaults", f, currentByKey, originByKey));

    const originPill = (origin) => origin === "default"
      ? '<span class="pill pill-dim">default</span>'
      : `${statusPill(origin.startsWith("sidecar") ? "warn" : "ok").__raw} ${escapeHtml(origin)}`;
    const rows = r.fields.map((f) => render`
      <tr><td class="mono">${f.key}</td><td class="mono">${JSON.stringify(f.value)}</td><td>${raw(originPill(f.origin))}</td></tr>
    `);

    box.innerHTML = render`
      <h3 class="modal-title" id="modal-title">Settings</h3>
      <p class="subtitle">Global project configuration (table-tool.toml) — applies to every table that has no sidecar of its own.</p>
      <div class="settings-modal-scroll">
        <div class="card settings-section">
          <h2 class="settings-section-title">Default paths and formats</h2>
          <p class="settings-section-desc">Used for every table that has no explicit command-line preference.</p>
          ${defaultsRows}
          <div class="settings-toolbar">
            <span class="settings-toolbar-status" id="cfg-dirty-status">No changes</span>
            <button type="button" id="cfg-reset">${icon("refresh")}Reset</button>
          </div>
        </div>
        <div class="card">
          <h2 class="settings-section-title">TOML preview</h2>
          <pre class="settings-preview" id="cfg-preview"></pre>
        </div>
        <div class="card">
          <h2 class="settings-section-title">Detailed resolution (default → global → sidecar)</h2>
          <div class="table-scroll">
            <table><thead><tr><th>Field</th><th>Value</th><th>Origin</th></tr></thead><tbody>${rows}</tbody></table>
          </div>
        </div>
      </div>
      <div class="modal-actions">
        <button type="button" id="cfg-cancel">Close</button>
        <button class="primary" id="cfg-save" disabled>${icon("save")}Save</button>
      </div>
    `;
    overlay.hidden = false;

    const originalValues = _cfgReadFormValues(schema);
    const preview = document.getElementById("cfg-preview");
    const dirtyStatus = document.getElementById("cfg-dirty-status");
    const saveBtn = document.getElementById("cfg-save");
    let unsavedCount = 0;

    const fieldOriginal = (section, key) => originalValues.defaults[key];

    const refresh = () => {
      const values = _cfgReadFormValues(schema);
      preview.textContent = _cfgTomlPreview(values);
      let changedCount = 0;
      document.querySelectorAll(".settings-row").forEach((row) => {
        const [section, ...rest] = row.dataset.rowKey.split(".");
        const fieldKey = rest.join(".");
        const current = values.defaults[fieldKey];
        const changed = JSON.stringify(current) !== JSON.stringify(fieldOriginal(section, fieldKey));
        row.querySelector(".settings-row-dirty-note").hidden = !changed;
        if (changed) changedCount += 1;
      });
      unsavedCount = changedCount;
      dirtyStatus.textContent = changedCount ? `${changedCount} unsaved changes` : "No changes";
      dirtyStatus.className = "settings-toolbar-status" + (changedCount ? " settings-toolbar-status-dirty" : "");
      saveBtn.disabled = changedCount === 0;
    };
    document.querySelectorAll("[data-cfg-field]").forEach((el) => el.addEventListener("input", refresh));
    refresh();

    // unsaved-changes guard while the modal is open (navigation and
    // beforeunload both consult it)
    registerDirtyGuard("settings-modal", {
      message: "The project configuration has unsaved changes.",
      isDirty: () => unsavedCount > 0,
    });

    const close = async () => {
      if (unsavedCount > 0) {
        const ok = await confirmDialog("Discard unsaved settings?", { danger: true, confirmLabel: "Discard" });
        if (!ok) return;
      }
      removeDirtyGuard("settings-modal");
      overlay.hidden = true;
      box.classList.remove("modal-large");
      box.innerHTML = "";
    };

    document.getElementById("cfg-cancel").onclick = close;
    document.getElementById("cfg-reset").onclick = () => build(); // fresh copy from disk
    document.getElementById("cfg-save").onclick = async () => {
      try {
        await api("/api/config", { method: "PUT", body: _cfgReadFormValues(schema) });
        toast("Global configuration saved", "ok");
        removeDirtyGuard("settings-modal");
        overlay.hidden = true;
        box.classList.remove("modal-large");
        box.innerHTML = "";
      } catch (e) {
        toastError(e);
      }
    };
  };

  await build();
}

export { openSettingsModal };
