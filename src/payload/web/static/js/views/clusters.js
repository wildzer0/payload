/* Clusters view (route "/clusters"): list/create/edit/delete of
 * [[cluster]] declarations. One cluster per table (see
 * src/payload/docs/CLUSTERS.md): a named bundle of defaults/plugin
 * overrides a table can opt into, sitting between the project's global
 * [defaults] and a table's own sidecar/batch-inline overrides. */
"use strict";

import {
  escapeHtml, raw, render, icon, iconSpan, toast, toastError,
  confirmDialog, openDialog, pageHeader, val,
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

  // create/edit cluster form, shared by the "New cluster" header button
  // and each card's edit action — a modal, so the grid stays visible
  const openClusterForm = (cluster) => {
    const editing = !!cluster;
    const actions = [];
    if (editing) actions.push({ label: "Cancel" });
    const formBody = render`
      <div class="field"><label>Name</label><input type="text" id="cf-name" placeholder="sensors" ${editing ? "disabled" : ""}></div>
      <div class="field-row">
        <div class="field"><label>Writer</label><input type="text" id="cf-writer" placeholder="no override"></div>
        <div class="field"><label>Reader</label><input type="text" id="cf-reader" placeholder="no override"></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Output folder</label><input type="text" id="cf-output-dir" placeholder="no override"></div>
        <div class="field"><label>Cache folder</label><input type="text" id="cf-cache-dir" placeholder="no override"></div>
      </div>
      <div class="field">
        <label>Byte order</label>
        <select id="cf-byte-order"><option value="">no override</option><option value="little">little</option><option value="big">big</option></select>
      </div>
    `;
    const dialog = openDialog({
      title: editing ? `Edit cluster '${cluster.name}'` : "New cluster",
      body: formBody,
      actions: [{ label: editing ? "Save changes" : "Create cluster", className: "primary", autofocus: true, value: true }],
    });
    if (editing) {
      document.getElementById("cf-name").value = cluster.name;
      document.getElementById("cf-writer").value = cluster.defaults.writer || "";
      document.getElementById("cf-reader").value = cluster.defaults.reader || "";
      document.getElementById("cf-output-dir").value = cluster.defaults.output_dir || "";
      document.getElementById("cf-cache-dir").value = cluster.defaults.cache_dir || "";
      document.getElementById("cf-byte-order").value = cluster.defaults.byte_order || "";
    }
    dialog.then(async (result) => {
      if (result !== true) return; // cancelled
      const name = val("cf-name");
      if (!name) { toast("Enter a cluster name first", "warn"); return; }
      const defaults = {};
      for (const [key, id] of [["writer", "cf-writer"], ["reader", "cf-reader"], ["output_dir", "cf-output-dir"], ["cache_dir", "cf-cache-dir"]]) {
        const v = val(id);
        if (v) defaults[key] = v;
      }
      const byteOrder = document.getElementById("cf-byte-order").value;
      if (byteOrder) defaults.byte_order = byteOrder;
      try {
        if (editing) {
          await api(`/api/clusters/${encodeURIComponent(cluster.name)}`, { method: "PUT", body: { defaults } });
          toast(`Cluster '${cluster.name}' updated`, "ok");
        } else {
          await api("/api/clusters", { method: "POST", body: { name, defaults } });
          toast(`Cluster '${name}' created`, "ok");
        }
        viewClusters();
      } catch (e) {
        toastError(e);
      }
    });
  };

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Clusters", "A cluster bundles config overrides that a table can opt into — global → cluster → sidecar → CLI flags. Assigning a table to a cluster happens on the table page.", render`<button class="btn primary" id="btn-new-cluster">${icon("plus")}New cluster</button>`))}
    <div class="cluster-grid" id="cluster-grid">${raw(cards || '<p class="empty-state card">No cluster declared in this project.</p>')}</div>
  `;

  document.getElementById("btn-new-cluster").onclick = () => openClusterForm(null);

  document.querySelectorAll("[data-edit-cluster]").forEach((btn) => {
    btn.onclick = () => {
      const c = r.clusters.find((x) => x.name === btn.dataset.editCluster);
      if (c) openClusterForm(c);
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