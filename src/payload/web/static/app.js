/* payload web UI — entry point (ES module). Router on location.hash,
 * bootstrap of the theme toggle, the delegated <details> animation
 * listener, and the sidebar root-path readout. Views and helpers now
 * live in js/ (api.js, ui.js, markdown.js, views/*.js) — this file
 * only wires them together. */
"use strict";

import { initTheme, toggleTheme, _animateDetailsToggle, skeletonLoading, emptyCard, toastError } from "./js/ui.js";
import { api } from "./js/api.js";
import { viewDashboard } from "./js/views/dashboard.js";
import { viewTable } from "./js/views/table.js";
import { viewBuildAll } from "./js/views/build_all.js";
import { viewPlugins, viewPluginDetail, viewLocalPluginEditor } from "./js/views/plugins.js";
import { viewClusters } from "./js/views/clusters.js";
import { viewDoctor } from "./js/views/doctor.js";
import { viewConfig } from "./js/views/config.js";
import { viewTools } from "./js/views/tools.js";
import { viewDocsList, viewDocDetail } from "./js/views/docs.js";

/* ---------- router ---------- */

const ROUTES = [
  [/^\/$/, viewDashboard],
  [/^\/table\/([^/]+)$/, viewTable],
  [/^\/build-all$/, viewBuildAll],
  [/^\/plugins$/, viewPlugins],
  [/^\/plugin\/([^/]+)$/, viewPluginDetail],
  [/^\/clusters$/, viewClusters],
  [/^\/local-plugin\/([^/]+)$/, viewLocalPluginEditor],
  [/^\/doctor$/, viewDoctor],
  [/^\/config$/, viewConfig],
  [/^\/tools$/, viewTools],
  [/^\/docs$/, viewDocsList],
  [/^\/docs\/([^/]+)$/, viewDocDetail],
];

async function router() {
  const path = (location.hash || "#/").slice(1) || "/";
  document.querySelectorAll(".nav a").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("data-route") === "/" + path.split("/")[1] || (path === "/" && a.getAttribute("data-route") === "/"));
  });
  for (const [pattern, handler] of ROUTES) {
    const m = path.match(pattern);
    if (m) {
      const content = document.getElementById("content");
      content.innerHTML = skeletonLoading();
      try {
        await handler(...m.slice(1));
      } catch (e) {
        content.innerHTML = emptyCard(e.message, e.hint);
        toastError(e);
        return;
      }
      content.classList.remove("route-fade");
      void content.offsetWidth; // restarts the CSS animation even on repeated routes
      content.classList.add("route-fade");
      return;
    }
  }
  document.getElementById("content").innerHTML = '<p class="empty-state">Page not found.</p>';
}

window.addEventListener("hashchange", router);

/* ---------- bootstrap ---------- */

initTheme();

document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

document.getElementById("content").addEventListener("click", (e) => {
  const summary = e.target.closest("summary");
  const details = summary && summary.parentElement;
  if (!details || details.tagName !== "DETAILS") return;
  e.preventDefault();
  _animateDetailsToggle(details);
});

api("/api/health").then((r) => {
  document.getElementById("root-path").textContent = r.root;
  document.getElementById("root-path").title = r.root;
}).catch(() => {});

router();
