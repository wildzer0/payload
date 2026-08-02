/* Activity log view (route "/log"): the project-wide timeline of
 * builds, commits, golden changes and file-browser operations — newest
 * first (see /api/log/activity and core/activity.py). */
"use strict";

import { escapeHtml, raw, render, pageHeader, iconSpan } from "../ui.js";
import { api } from "../api.js";

const PAGE_SIZE = 50;

const KIND_STYLE = {
  build: "pill-ok",
  commit: "pill-current",
  golden: "pill-golden",
  fs: "pill-dim",
};
const KIND_LABEL = { build: "build", commit: "commit", golden: "golden", fs: "files" };

function _eventHtml(e) {
  const cls = KIND_STYLE[e.kind] || "pill-dim";
  const label = KIND_LABEL[e.kind] || e.kind;
  const level = e.level === "fail" ? " pill-fail" : e.level === "warn" ? " pill-warn" : "";
  const ts = new Date(e.ts * 1000);
  const time = ts.toLocaleString();
  return `
    <div class="activity-item">
      <span class="pill ${cls}${level}">${escapeHtml(label)}</span>
      <span class="activity-detail">${escapeHtml(e.detail)}</span>
      <span class="activity-time mono">${escapeHtml(time)}</span>
    </div>`;
}

async function viewLog() {
  const content = document.getElementById("content");
  content.innerHTML = render`
    ${raw(pageHeader("Activity", "Project-wide timeline: builds, commits, golden changes and file-browser operations."))}
    <div class="card">
      <div class="activity-list" id="activity-list"></div>
      ${raw(`<button class="ghost" id="activity-more" hidden>${iconSpan("down")}Load more</button>`)}
    </div>
  `;

  let offset = 0;
  const list = document.getElementById("activity-list");
  const moreBtn = document.getElementById("activity-more");

  const loadMore = async () => {
    try {
      const r = await api(`/api/log/activity?limit=${PAGE_SIZE}&offset=${offset}`);
      if (!r.events.length && offset === 0) {
        list.innerHTML = '<p class="empty-state">No activity yet — run a build, commit, or touch a file.</p>';
        return;
      }
      if (offset === 0) list.textContent = "";
      r.events.forEach((e) => list.insertAdjacentHTML("beforeend", _eventHtml(e)));
      offset += r.events.length;
      moreBtn.hidden = offset >= r.total;
    } catch (e) {
      list.innerHTML = `<p class="empty-state">${escapeHtml(e.message)}</p>`;
    }
  };

  moreBtn.onclick = loadMore;
  await loadMore();
}

export { viewLog };
