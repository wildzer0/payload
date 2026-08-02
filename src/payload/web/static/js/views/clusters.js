/* Clusters view (route "/clusters"): list/create/edit/delete of
 * [[cluster]] declarations. One cluster per table (see
 * src/payload/docs/CLUSTERS.md): a named bundle of defaults/plugin
 * overrides a table can opt into, sitting between the project's global
 * [defaults] and a table's own sidecar/batch-inline overrides. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, pageHeader, val,
} from "../ui.js";
import { api } from "../api.js";

function _clusterCardHtml(c) {
  const overrideParts = [
    ...Object.entries(c.defaults).map(([k, v]) => `<span class="pill pill-dim mono">${escapeHtml(k)}=${escapeHtml(v)}</span>`),
    ...Object.keys(c.plugin).map((k) => `<span class="pill pill-dim mono">plugin.${escapeHtml(k)}</span>`),
  ];
  // a cluster can have hundreds of tables: rendering every member as a
  // pill would make the card a wall of names — show the first few and
  // summarize the rest with a count (full list in the tooltip)
  const MAX_MEMBERS_SHOWN = 8;
  const members = c.members || [];
  const shown = members.slice(0, MAX_MEMBERS_SHOWN);
  const overflow = members.length - shown.length;
  const membersHtml = members.length
    ? `
    <div class="cluster-card-members">
      <span class="cluster-card-members-label">Members</span>
      ${shown.map((m) => `<span class="pill pill-dim">${escapeHtml(m)}</span>`).join("")}
      ${overflow > 0 ? `<span class="pill pill-dim cluster-card-more" title="${escapeHtml(members.join(", "))}">+${overflow} more</span>` : ""}
    </div>`
    : "";
  return `
    <div class="card cluster-card">
      <div class="cluster-card-head">
        <strong>${escapeHtml(c.name)}</strong>
        <span class="pill pill-current">${c.member_count} table${c.member_count === 1 ? "" : "s"}</span>
        <div class="cluster-card-actions">
          <button class="icon-only" data-edit-cluster="${escapeHtml(c.name)}" title="Edit">${iconSpan("edit")}</button>
          <button class="danger icon-only" data-delete-cluster="${escapeHtml(c.name)}" title="Delete">${iconSpan("trash")}</button>
        </div>
      </div>
      <div class="cluster-card-body">
        <div class="cluster-card-overrides">${overrideParts.join("") || '<span class="cluster-card-empty">No override — inherits the global defaults</span>'}</div>
        ${membersHtml}
      </div>
    </div>`;
}

async function viewClusters() {
  const r = await api("/api/clusters");
  const cards = r.clusters.map(_clusterCardHtml).join("");

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Clusters", "A cluster bundles config overrides that a table can opt into — global → cluster → sidecar → CLI flags. Assigning a table to a cluster happens on the table page."))}
    <div class="card" id="cluster-form-card">
      <h2 class="card-title" id="cluster-form-title">New cluster</h2>
      <div class="field"><label>Name</label><input type="text" id="cf-name" placeholder="sensors"></div>
      <div class="field-row">
        <div class="field"><label>Writer</label><input type="text" id="cf-writer" placeholder="no override"></div>
        <div class="field"><label>Reader</label><input type="text" id="cf-reader" placeholder="no override"></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Output folder</label><input type="text" id="cf-output-dir" placeholder="no override"></div>
        <div class="field"><label>Cache folder</label><input type="text" id="cf-cache-dir" placeholder="no override"></div>
      </div>
      <div class="field-row">
        <div class="field">
          <label>Byte order</label>
          <select id="cf-byte-order"><option value="">no override</option><option value="little">little</option><option value="big">big</option></select>
        </div>
      </div>
      <div class="build-actions">
        <button class="primary" id="cf-save">${icon("plus")}Create cluster</button>
        <button id="cf-cancel" hidden>Cancel edit</button>
      </div>
    </div>
    <div class="cluster-grid" id="cluster-grid">${raw(cards || '<p class="empty-state card">No cluster declared in this project.</p>')}</div>
  `;

  let editingName = null;

  const resetForm = () => {
    editingName = null;
    document.getElementById("cluster-form-title").textContent = "New cluster";
    document.getElementById("cf-save").innerHTML = `${iconSpan("plus")}Create cluster`;
    document.getElementById("cf-cancel").hidden = true;
    document.getElementById("cf-name").disabled = false;
    ["cf-name", "cf-writer", "cf-reader", "cf-output-dir", "cf-cache-dir"].forEach((id) => { document.getElementById(id).value = ""; });
    document.getElementById("cf-byte-order").value = "";
  };

  document.getElementById("cf-cancel").onclick = resetForm;

  document.getElementById("cf-save").onclick = async () => {
    const name = editingName || val("cf-name");
    if (!name) { toast("Enter a cluster name first", "warn"); return; }
    const defaults = {};
    const writer = val("cf-writer"); if (writer) defaults.writer = writer;
    const reader = val("cf-reader"); if (reader) defaults.reader = reader;
    const outputDir = val("cf-output-dir"); if (outputDir) defaults.output_dir = outputDir;
    const cacheDir = val("cf-cache-dir"); if (cacheDir) defaults.cache_dir = cacheDir;
    const byteOrder = document.getElementById("cf-byte-order").value; if (byteOrder) defaults.byte_order = byteOrder;

    try {
      if (editingName) {
        await api(`/api/clusters/${encodeURIComponent(editingName)}`, { method: "PUT", body: { defaults } });
        toast(`Cluster '${editingName}' updated`, "ok");
      } else {
        await api("/api/clusters", { method: "POST", body: { name, defaults } });
        toast(`Cluster '${name}' created`, "ok");
      }
      viewClusters();
    } catch (e) {
      toastError(e);
    }
  };

  document.querySelectorAll("[data-edit-cluster]").forEach((btn) => {
    btn.onclick = () => {
      const c = r.clusters.find((x) => x.name === btn.dataset.editCluster);
      if (!c) return;
      editingName = c.name;
      document.getElementById("cluster-form-title").textContent = `Edit '${c.name}'`;
      document.getElementById("cf-save").innerHTML = `${iconSpan("save")}Save changes`;
      document.getElementById("cf-cancel").hidden = false;
      document.getElementById("cf-name").value = c.name;
      document.getElementById("cf-name").disabled = true;
      document.getElementById("cf-writer").value = c.defaults.writer || "";
      document.getElementById("cf-reader").value = c.defaults.reader || "";
      document.getElementById("cf-output-dir").value = c.defaults.output_dir || "";
      document.getElementById("cf-cache-dir").value = c.defaults.cache_dir || "";
      document.getElementById("cf-byte-order").value = c.defaults.byte_order || "";
      document.getElementById("cluster-form-card").scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });

  document.querySelectorAll("[data-delete-cluster]").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.deleteCluster;
      const ok = await confirmDialog(`Delete cluster '${name}'?`, { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      try {
        await api(`/api/clusters/${encodeURIComponent(name)}`, { method: "DELETE" });
        toast(`Cluster '${name}' deleted`, "ok");
        viewClusters();
      } catch (e) {
        if (e.data && e.data.error === "ClusterError") {
          const ok2 = await confirmDialog(`${e.message}. Delete anyway and clear it from those tables (tags kept)?`, { danger: true, confirmLabel: "Delete anyway" });
          if (!ok2) return;
          try {
            await api(`/api/clusters/${encodeURIComponent(name)}?force=true`, { method: "DELETE" });
            toast(`Cluster '${name}' deleted`, "ok");
            viewClusters();
          } catch (e2) {
            toastError(e2);
          }
          return;
        }
        toastError(e);
      }
    };
  });
}

export { viewClusters };
