/* Doctor view (route "/doctor"): system status check results, grouped
 * by OK/warn/fail. Split out of the former single-file app.js — no
 * behavior change. */
"use strict";

import { raw, render, pageHeader, ICONS } from "../ui.js";
import { api } from "../api.js";

const DOCTOR_STATUS_ICON = { ok: "check", warn: "warnTri", fail: "cross" };

async function viewDoctor() {
  const r = await api("/api/doctor");
  const counts = { ok: 0, warn: 0, fail: 0 };
  r.checks.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1; });

  const items = r.checks.map((c) => render`
    <div class="doctor-item doctor-item-${c.status}">
      <div class="doctor-item-icon">${raw(ICONS[DOCTOR_STATUS_ICON[c.status]] || ICONS.dash)}</div>
      <div class="doctor-item-body">
        <div class="doctor-item-name">${c.name.toUpperCase()}</div>
        <div class="doctor-item-message">${c.message}</div>
        ${raw(c.hint ? render`<div class="doctor-item-hint">${c.hint}</div>` : "")}
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Doctor", "System status check (toolchain, plugins, config, and directories)."))}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Total checks</div><div class="stat-value">${r.checks.length}</div></div>
      <div class="stat-card"><div class="stat-label">OK</div><div class="stat-value">${counts.ok}</div></div>
      <div class="stat-card ${counts.warn ? "stat-warn" : ""}"><div class="stat-label">Warning</div><div class="stat-value">${counts.warn}</div></div>
      <div class="stat-card ${counts.fail ? "stat-fail" : ""}"><div class="stat-label">Failed</div><div class="stat-value">${counts.fail}</div></div>
    </div>
    <div class="doctor-list">${items.length ? items : ['<p class="empty-state">No check registered.</p>']}</div>
  `;
}

export { viewDoctor };
