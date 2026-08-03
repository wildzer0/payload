/* Batch tables view (route "/batch"): manage [[batch_table]] entries
 * without touching table-tool.toml by hand — create, edit members,
 * reader/writer/byte_order, delete. Mirrors the CLI's batch flows. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, pageHeader,
} from "../ui.js";
import { api, getPlugins } from "../api.js";

// minimal escaping for a quoted attribute value in a selector
// (CSS.escape is a browser-only API, unavailable in jsdom/jsc harnesses)
const _escAttr = (v) => String(v).replace(/["\\]/g, "\\$&");

const _optionsHtml = (options, current) => `
  <option value="">auto</option>
  ${options.map((o) => `<option value="${escapeHtml(o)}"${o === current ? " selected" : ""}>${escapeHtml(o)}</option>`).join("")}
`;

async function viewBatch() {
  const [batchResp, candResp, plugins] = await Promise.all([
    api("/api/batch"), api("/api/batch/candidates"), getPlugins(),
  ]);
  // only table-source candidates (config/sidecars/hidden/internal dirs
  // are excluded server-side) — not every file in the project folder
  const projectFiles = (candResp.files || []).map((f) => f);
  const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
  const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);

  const content = document.getElementById("content");
  content.innerHTML = render`
    ${raw(pageHeader("Batch tables", "Created by dragging several files onto the Dashboard (or here, from project files). This page manages their members and overrides — no table-tool.toml editing by hand."))}
    <div class="card">
      <h2>New batch table</h2>
      <p class="subtitle">From files already in the project that aren't single tables yet — the Dashboard import is the main way to bring new files in.</p>
      <div class="field-row">
        <div class="field">
          <label>Name</label>
          <input type="text" id="bt-new-name" class="mono" placeholder="batch_name">
        </div>
        <div class="field">
          <label>Add members</label>
          <div class="batch-member-row">
            <select id="bt-new-member" class="inline-select"><option value="">— choose a file —</option>${raw(projectFiles.map((f) => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join(""))}</select>
            <button id="bt-new-add">${icon("plus")}Add</button>
          </div>
        </div>
      </div>
      <div id="bt-new-members" class="batch-members"></div>
      <div class="build-actions"><button class="primary" id="bt-new-create">${icon("plus")}Create batch table</button></div>
    </div>
    <div id="batch-list"></div>
  `;

  // ---- new batch table ----
  const newSources = [];
  const newMembersEl = document.getElementById("bt-new-members");
  const renderNewMembers = () => {
    newMembersEl.innerHTML = newSources.length
      ? newSources.map((s) => `
          <span class="pill pill-dim">${escapeHtml(s)} <button type="button" class="chip-remove" data-rm="${escapeHtml(s)}" aria-label="Remove">×</button></span>`).join("")
      : '<span class="table-tags-empty">No member yet — pick files above.</span>';
    newMembersEl.querySelectorAll("[data-rm]").forEach((btn) => {
      btn.onclick = () => { newSources.splice(newSources.indexOf(btn.dataset.rm), 1); renderNewMembers(); };
    });
  };
  renderNewMembers();
  document.getElementById("bt-new-add").onclick = () => {
    const sel = document.getElementById("bt-new-member");
    if (sel.value && !newSources.includes(sel.value)) newSources.push(sel.value);
    renderNewMembers();
  };
  document.getElementById("bt-new-create").onclick = async () => {
    const name = document.getElementById("bt-new-name").value.trim();
    if (!name) { toast("Batch table name can't be empty", "warn"); return; }
    if (!newSources.length) { toast("Add at least one member file", "warn"); return; }
    try {
      await api("/api/batch", { method: "POST", body: { name, sources: newSources } });
      toast(`Batch table '${name}' created`, "ok");
      viewBatch();
    } catch (e) { toastError(e); }
  };

  // ---- existing batch tables ----
  const list = document.getElementById("batch-list");
  if (!batchResp.batches.length) {
    list.innerHTML = '<p class="empty-state card">No batch table yet — create one above.</p>';
    return;
  }

  list.innerHTML = batchResp.batches.map((b) => `
    <div class="card batch-card" data-batch="${escapeHtml(b.name)}">
      <div class="fs-detail-head">
        <div>
          <h2 class="m-0">${escapeHtml(b.name)}</h2>
          <div class="fs-detail-meta">
            <span class="meta-chip"><strong>Members</strong><span class="mono" id="bc-${escapeHtml(b.name)}-count">${b.sources.length}</span></span>
            <span class="meta-chip"><strong>Path</strong><span class="mono">[[batch_table]] in table-tool.toml</span></span>
          </div>
        </div>
        <div class="fs-detail-actions">
          <a class="btn" href="#/table/${encodeURIComponent(b.name)}">${iconSpan("box")}Open table</a>
          <button class="danger" id="bc-${escapeHtml(b.name)}-delete">${iconSpan("trash")}Delete</button>
        </div>
      </div>
      <div class="field-row">
        <div class="field">
          <label>Reader</label>
          <select class="inline-select bc-reader" data-name="${escapeHtml(b.name)}">${_optionsHtml(readerNames, b.reader || "")}</select>
        </div>
        <div class="field">
          <label>Writer</label>
          <select class="inline-select bc-writer" data-name="${escapeHtml(b.name)}">${_optionsHtml(writerNames, b.writer || "")}</select>
        </div>
        <div class="field">
          <label>Byte order</label>
          <select class="inline-select bc-byteorder" data-name="${escapeHtml(b.name)}">
            <option value="">default</option>
            <option value="little"${b.byte_order === "little" ? " selected" : ""}>little</option>
            <option value="big"${b.byte_order === "big" ? " selected" : ""}>big</option>
          </select>
        </div>
        <div class="field bc-add-field">
          <label>Add member</label>
          <div class="batch-member-row">
            <select class="inline-select bc-member" data-name="${escapeHtml(b.name)}"><option value="">— choose a file —</option>${projectFiles.map((f) => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("")}</select>
            <button class="bc-add" data-name="${escapeHtml(b.name)}">${iconSpan("plus")}Add</button>
          </div>
        </div>
      </div>
      <div class="batch-members bc-members" data-name="${escapeHtml(b.name)}"></div>
      <div class="build-actions"><button class="primary bc-save" data-name="${escapeHtml(b.name)}">${iconSpan("save")}Save</button></div>
    </div>
  `).join("");

  // wire each card: members chips + add/remove, save, delete
  const stateByName = new Map();
  batchResp.batches.forEach((b) => stateByName.set(b.name, [...b.sources]));

  const renderMembers = (name) => {
    const sources = stateByName.get(name) || [];
    const el = document.querySelector(`.bc-members[data-name="${_escAttr(name)}"]`);
    if (!el) return;
    el.innerHTML = sources.length
      ? sources.map((s) => `
          <span class="pill pill-dim">${escapeHtml(s)} <button type="button" class="chip-remove" data-rm="${escapeHtml(s)}" data-name="${escapeHtml(name)}" aria-label="Remove">×</button></span>`).join("")
      : '<span class="table-tags-empty">No members — a batch table needs at least one file.</span>';
    document.querySelector(`#bc-${_escAttr(name)}-count`).textContent = sources.length;
    el.querySelectorAll("[data-rm]").forEach((btn) => {
      btn.onclick = () => {
        const arr = stateByName.get(name);
        const i = arr.indexOf(btn.dataset.rm);
        if (i >= 0) arr.splice(i, 1);
        renderMembers(name);
      };
    });
  };
  batchResp.batches.forEach((b) => renderMembers(b.name));

  document.querySelectorAll(".bc-add").forEach((btn) => {
    btn.onclick = () => {
      const name = btn.dataset.name;
      const sel = document.querySelector(`.bc-member[data-name="${_escAttr(name)}"]`);
      if (sel && sel.value && !stateByName.get(name).includes(sel.value)) {
        stateByName.get(name).push(sel.value);
        renderMembers(name);
      }
    };
  });

  document.querySelectorAll(".bc-save").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.name;
      btn.disabled = true;
      try {
        const sources = stateByName.get(name) || [];
        if (!sources.length) { toast("A batch table needs at least one member", "warn"); btn.disabled = false; return; }
        const reader = document.querySelector(`.bc-reader[data-name="${_escAttr(name)}"]`).value;
        const writer = document.querySelector(`.bc-writer[data-name="${_escAttr(name)}"]`).value;
        const byteOrder = document.querySelector(`.bc-byteorder[data-name="${_escAttr(name)}"]`).value;
        await api("/api/batch/" + encodeURIComponent(name), {
          method: "PUT",
          body: { sources, reader: reader || null, writer: writer || null, byte_order: byteOrder || null },
        });
        toast(`Batch table '${name}' saved`, "ok");
      } catch (e) { toastError(e); }
      btn.disabled = false;
    };
  });

  // delete buttons (targeted by the literal id pattern)
  batchResp.batches.forEach((b) => {
    const del = document.getElementById("bc-" + b.name + "-delete");
    if (del) {
      del.onclick = async () => {
        const ok = await confirmDialog(`Delete the batch table '${b.name}'? Its member files stay on disk.`, { danger: true, confirmLabel: "Delete" });
        if (!ok) return;
        try {
          await api("/api/batch/" + encodeURIComponent(b.name), { method: "DELETE" });
          toast(`Batch table '${b.name}' deleted`, "ok");
          viewBatch();
        } catch (e) { toastError(e); }
      };
    }
  });
}

export { viewBatch };
