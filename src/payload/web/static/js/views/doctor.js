/* Doctor view (route "/doctor"): system status check results, ordered
 * by severity — a status banner up top, then Failures / Warnings
 * sections, and the passing checks collapsed by default (they're the
 * noise; "everything is fine" is the banner). A "Re-run" re-checks. */
"use strict";

import { escapeHtml, iconSpan, pageHeader, statusPill } from "../ui.js";
import { api } from "../api.js";

async function viewDoctor() {
  const r = await api("/api/doctor");
  const fail = r.checks.filter((c) => c.status === "fail");
  const warn = r.checks.filter((c) => c.status === "warn");
  const ok = r.checks.filter((c) => c.status === "ok");
  const nFail = fail.length;
  const nWarn = warn.length;
  const nOk = ok.length;
  const okAll = nFail === 0 && nWarn === 0;

  const itemHtml = (c) => `
    <div class="doctor-row doctor-row-${c.status}">
      ${statusPill(c.status).__raw}
      <div class="doctor-row-body">
        <div class="doctor-row-name">${escapeHtml(c.name)}</div>
        <div class="doctor-row-message">${escapeHtml(c.message)}</div>
        ${c.hint ? `<div class="doctor-row-hint">${escapeHtml(c.hint)}</div>` : ""}
      </div>
    </div>`;

  const section = (cls, title, icon, items) => items.length
    ? `
    <div class="doctor-section doctor-section-${cls}">
      <h3 class="doctor-section-title">${iconSpan(icon)}${escapeHtml(title)}</h3>
      ${items.map(itemHtml).join("")}
    </div>`
    : "";

  const banner = `
    <div class="doctor-banner doctor-banner-${okAll ? "ok" : (nFail ? "fail" : "warn")}">
      ${iconSpan(okAll ? "check" : (nFail ? "cross" : "warnTri"))}
      <div class="doctor-banner-text">
        <strong>${okAll ? "All systems nominal" : (nFail ? `${nFail} check${nFail === 1 ? "" : "s"} failed` : `${nWarn} warning${nWarn === 1 ? "" : "s"}`)}</strong>
        <span>${nOk} ok · ${nWarn} warnings · ${nFail} failed</span>
      </div>
      <span class="flex-1"></span>
      <button id="doctor-rerun">${iconSpan("refresh")}Re-run</button>
    </div>`;

  document.getElementById("content").innerHTML = `
    ${pageHeader("Doctor", "System status check (toolchain, plugins, config, and directories).")}
    ${banner}
    ${section("fail", "Failures", "cross", fail)}
    ${section("warn", "Warnings", "warnTri", warn)}
    ${section("ok", `Passed (${nOk})`, "check", ok)}
  `;

  const rerun = document.getElementById("doctor-rerun");
  if (rerun) rerun.onclick = () => viewDoctor();
}

export { viewDoctor };
