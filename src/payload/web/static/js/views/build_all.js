/* Build-all view (route "/build-all"): the SSE live log for a batch
 * build of every table (see routes/build.py). */
"use strict";

import { render, raw, pageHeader, icon, iconSpan, val, chk, attachAutocomplete } from "../ui.js";
import { getPlugins } from "../api.js";

const MAX_BUILD_ALL_JOBS = 32;

function _fmtSummary(d) {
  const parts = [`${d.built} built`, `${d.cached} from cache`];
  if (d.golden_mismatch) parts.push(`${d.golden_mismatch} golden mismatch`);
  if (d.errors) parts.push(`${d.errors} errors`);
  return parts.join(" · ");
}

function viewBuildAll() {
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Build all", "Build every table discovered under the project root."))}
    <div class="card">
      <h2 class="card-title">Configuration</h2>
      <div class="field-row">
        <div class="field">
          <label>Writer</label>
          <div class="autocomplete-wrap"><input type="text" id="ba-to" placeholder="default"></div>
        </div>
        <div class="field">
          <label>Filter (glob)</label>
          <input type="text" id="ba-filter" placeholder="sensors/**">
        </div>
        <div class="field">
          <label>Jobs (1–${MAX_BUILD_ALL_JOBS})</label>
          <input type="number" id="ba-jobs" value="1" min="1" max="${MAX_BUILD_ALL_JOBS}" class="w-80">
        </div>
      </div>
      <div class="toggle-chip-row">
        <label class="toggle-chip"><input type="checkbox" id="ba-force"><span>--force</span></label>
        <label class="toggle-chip"><input type="checkbox" id="ba-golden"><span>--check-golden</span></label>
      </div>
      <div class="build-actions">
        <button class="primary" id="ba-start">${icon("play")}Start build-all</button>
        <span class="subtitle" id="ba-status">Idle — the log below shows live progress.</span>
      </div>
    </div>
    <div class="card">
      <h2 class="card-title">Log</h2>
      <div class="log" id="ba-log"><span class="log-empty">Waiting…</span></div>
    </div>
  `;

  getPlugins().then((plugins) => {
    const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);
    attachAutocomplete(document.getElementById("ba-to"), () => writerNames);
  }).catch(() => {});

  const jobsInput = document.getElementById("ba-jobs");
  const clampJobs = () => {
    const n = Math.round(Number(jobsInput.value));
    jobsInput.value = String(Math.min(MAX_BUILD_ALL_JOBS, Math.max(1, Number.isFinite(n) && n > 0 ? n : 1)));
  };
  jobsInput.addEventListener("change", clampJobs);

  const statusEl = document.getElementById("ba-status");
  const setStatus = (html) => { statusEl.innerHTML = html; };

  document.getElementById("ba-start").onclick = () => {
    clampJobs();
    const log = document.getElementById("ba-log");
    log.innerHTML = "";
    const params = new URLSearchParams();
    if (val("ba-to")) params.set("to", val("ba-to"));
    if (val("ba-filter")) params.set("filter", val("ba-filter"));
    params.set("jobs", val("ba-jobs") || "1");
    if (chk("ba-force")) params.set("force", "true");
    if (chk("ba-golden")) params.set("check_golden", "true");

    const btn = document.getElementById("ba-start");
    btn.disabled = true;
    setStatus(`Running…`);
    const es = new EventSource("/api/build-all/stream?" + params.toString());
    const appendLine = (text, cls) => {
      const line = document.createElement("div");
      line.className = "log-line" + (cls ? " log-line-" + cls : "");
      line.textContent = text;
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
    };
    es.addEventListener("progress", (ev) => {
      const d = JSON.parse(ev.data);
      appendLine(`${d.status === "ok" ? "✓" : "✗"} ${d.source}`, d.status === "ok" ? "ok" : "fail");
    });
    es.addEventListener("summary", (ev) => {
      const d = JSON.parse(ev.data);
      appendLine(`— done: ${_fmtSummary(d)}`);
      setStatus(`Done — ${_fmtSummary(d)}`);
      es.close();
      btn.disabled = false;
    });
    es.addEventListener("error", (ev) => {
      if (ev.data) {
        const d = JSON.parse(ev.data);
        appendLine(`✗ ${d.message}`, "fail");
        setStatus(`Failed: ${d.message}`);
      }
      es.close();
      btn.disabled = false;
    });
  };
}

export { viewBuildAll };
