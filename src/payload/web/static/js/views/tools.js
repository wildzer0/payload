/* Tools view (route "/tools"): export .zip download and the clean
 * (cache/build/golden) confirmation flow. Split out of the former
 * single-file app.js — no behavior change. */
"use strict";

import { raw, render, pageHeader, icon, iconSpan, chk, confirmDialog, statusPill } from "../ui.js";
import { api } from "../api.js";

function viewTools() {
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Export & clean"))}
    <div class="card">
      <h2>Export</h2>
      <p class="subtitle">Download a .zip archive with the project's sources and config.</p>
      <div class="checkbox-row"><input type="checkbox" id="ex-history"><label for="ex-history">Include .payload_history/</label></div>
      <a class="btn" id="ex-download" href="#">Download .zip</a>
    </div>
    <div class="card">
      <h2>Clean</h2>
      <div class="field"><label>Target</label>
        <select id="cl-target"><option value="cache">cache</option><option value="build">build</option><option value="golden">golden</option><option value="all">all</option></select>
      </div>
      <button class="danger" id="cl-run">${icon("trash")}Clean</button>
      <div id="cl-result"></div>
    </div>
  `;
  document.getElementById("ex-download").onclick = (e) => {
    e.preventDefault();
    const q = chk("ex-history") ? "?include_history=true" : "";
    window.open("/api/export" + q, "_blank");
  };
  document.getElementById("cl-run").onclick = async () => {
    const target = document.getElementById("cl-target").value;
    const el = document.getElementById("cl-result");
    const preview = await api("/api/clean", { body: { target } });
    if (preview.status === "noop") { el.innerHTML = '<p class="empty-state">Nothing to clean.</p>'; return; }
    const ok = await confirmDialog(`Delete: ${preview.directories.join(", ")}?`, { danger: true, confirmLabel: "Delete" });
    if (!ok) return;
    const r = await api("/api/clean", { body: { target, confirm: true } });
    el.innerHTML = render`<p>${statusPill("ok")} removed: ${r.directories.join(", ")}</p>`;
  };
}

export { viewTools };
