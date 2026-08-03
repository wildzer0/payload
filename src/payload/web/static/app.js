/* payload web UI — entry point (ES module). Router on location.hash,
 * bootstrap of the theme toggle, the delegated <details> animation
 * listener, and the sidebar root-path readout. Views and helpers now
 * live in js/ (api.js, ui.js, markdown.js, views/*.js) — this file
 * only wires them together. */
"use strict";

import { initTheme, toggleTheme, _animateDetailsToggle, emptyCard, toastError, confirmDialog, openDialog, clearDirtyGuards, dirtyGuardActive } from "./js/ui.js";
import { api } from "./js/api.js";
import { viewDashboard } from "./js/views/dashboard.js";
import { viewTable } from "./js/views/table.js";
import { viewFiles } from "./js/views/files.js";
import { viewBatch } from "./js/views/batch.js";
import { viewLog } from "./js/views/log.js";
import { openPalette } from "./js/palette.js";
import { viewPlugins, viewPluginDetail, viewLocalPluginEditor } from "./js/views/plugins.js";
import { openSettingsModal } from "./js/views/config.js";
import { viewClusters } from "./js/views/clusters.js";
import { viewDoctor } from "./js/views/doctor.js";
import { viewTools } from "./js/views/tools.js";
import { viewDocsList, viewDocDetail } from "./js/views/docs.js";

/* ---------- router ---------- */

const ROUTES = [
  [/^\/$/, viewDashboard],
  [/^\/table\/([^/]+)$/, viewTable],
  [/^\/files(?:\/(.+))?$/, viewFiles],
  [/^\/batch$/, viewBatch],
  [/^\/log$/, viewLog],
  [/^\/plugins$/, viewPlugins],
  [/^\/plugin\/([^/]+)$/, viewPluginDetail],
  [/^\/clusters$/, viewClusters],
  [/^\/local-plugin\/([^/]+)$/, viewLocalPluginEditor],
  [/^\/doctor$/, viewDoctor],
  [/^\/tools$/, viewTools],
  [/^\/docs$/, viewDocsList],
  [/^\/docs\/([^/]+)$/, viewDocDetail],
];

async function router() {
  // every navigation renders a fresh page: guards registered by the
  // previous page (its editors are gone) must not linger
  clearDirtyGuards();
  // close any open modal (e.g. the full-screen text editor) and drop
  // its "large" sizing — a navigation supersedes whatever dialog the
  // previous page left open
  const modalOverlay = document.getElementById("modal-overlay");
  if (modalOverlay) modalOverlay.hidden = true;
  const modalBox = document.getElementById("modal-box");
  if (modalBox) modalBox.classList.remove("modal-large");
  const path = (location.hash || "#/").slice(1) || "/";
  document.querySelectorAll(".nav a").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("data-route") === "/" + path.split("/")[1] || (path === "/" && a.getAttribute("data-route") === "/"));
  });
  for (const [pattern, handler] of ROUTES) {
    const m = path.match(pattern);
    if (m) {
      const content = document.getElementById("content");
      // no skeleton, no fade, no "loading": each view renders its own
      // content directly when it's ready — the previous page stays
      // until then, so navigation never flashes a loading state
      try {
        // route params come URL-encoded (location.hash keeps the %XX);
        // decode them here so every handler gets the real name ("Un'altra
        // tabella", not "Un%27altra%20tabella") — re-encoded on the API
        // calls with encodeURIComponent
        await handler(...m.slice(1).map((p) => decodeURIComponent(p)));
      } catch (e) {
        content.innerHTML = emptyCard(e.message, e.hint);
        toastError(e);
        return;
      }
      return;
    }
  }
  document.getElementById("content").innerHTML = '<p class="empty-state">Page not found.</p>';
}

// navigation is serialized: a route change started while the previous
// view is still loading must not interleave with it, otherwise the
// SLOW view finishing last would overwrite the NEW page with its own
// content (e.g. the dashboard's report/clusters rendering clobbering a
// table page you just navigated to). The latest navigation always
// renders last.
let navigation = Promise.resolve();
function navigate() {
  navigation = navigation.then(() => router()).catch(() => {});
}

// the hash the app is currently showing — the dirty-guard wrapper
// reverts to it when the user declines "leave anyway"
let lastHash = location.hash || "#/";

window.addEventListener("hashchange", async () => {
  const target = location.hash || "#/";
  // same hash = a revert after a declined "leave anyway" (or a
  // same-route link, which normally doesn't even fire this): nothing
  // to do, the current page is still rendered
  if (target === lastHash) return;
  const guard = dirtyGuardActive();
  if (guard) {
    const ok = await confirmDialog(guard.message || "You have unsaved changes. Leave anyway?", { danger: true, confirmLabel: "Leave anyway" });
    if (!ok) {
      location.hash = lastHash; // the resulting hashchange hits the early-return branch above
      return;
    }
  }
  lastHash = target;
  navigate();
});

// covers close/refresh (hashchange can't), for the browsers that show
// the native prompt on a non-empty returnValue
window.addEventListener("beforeunload", (e) => {
  if (dirtyGuardActive()) {
    e.preventDefault();
    e.returnValue = "";
  }
});

/* ---------- bootstrap ---------- */

initTheme();

/* sidebar gear -> global settings modal (the old /config page) */
document.getElementById("settings-toggle").onclick = openSettingsModal;

/* global keyboard shortcuts:
 *   /  -> command palette       ?  -> this list
 *   g <key> -> jump (two-key navigation): g d dashboard, g f files,
 *   g l log, g t batch, g p plugins, g c clusters,
 *   g o doctor, g x config, g e export & clean, g h docs
 * Ignored while typing in an input/textarea/select/contenteditable. */
const NAV_SHORTCUTS = {
  d: "#/", f: "#/files", l: "#/log", t: "#/batch",
  p: "#/plugins", c: "#/clusters", o: "#/doctor", x: "#/config", e: "#/tools", h: "#/docs",
};
const _isTypingTarget = (el) => !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);

function openShortcutsHelp() {
  const rows = [
    ["/", "Command palette"], ["?", "This help"],
    ["g d", "Dashboard"], ["g f", "Files"], ["g l", "Activity"],
    ["g b", "Build all"], ["g t", "Batch tables"], ["g p", "Plugins"],
    ["g c", "Clusters"], ["g o", "Doctor"], ["g x", "Configuration"],
    ["g e", "Export & clean"], ["g h", "Documentation"],
  ];
  openDialog({
    title: "Keyboard shortcuts",
    body: `<div class="kbd-table">${rows.map(([k, label]) => `<div class="kbd-row"><span class="kbd">${k}</span><span>${label}</span></div>`).join("")}</div>`,
    actions: [{ label: "Close", autofocus: true }],
  });
}

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
    ev.preventDefault();
    openPalette();
    return;
  }
  if (ev.key === "/" && !_isTypingTarget(ev.target)) {
    ev.preventDefault();
    openPalette();
    return;
  }
  if (ev.key === "?" && !_isTypingTarget(ev.target)) {
    ev.preventDefault();
    openShortcutsHelp();
    return;
  }
  // two-key navigation: "g" arms, the next key jumps
  if (ev.key === "g" && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !_isTypingTarget(ev.target)) {
    window.__gNavArmed = true;
    setTimeout(() => { window.__gNavArmed = false; }, 1500);
    return;
  }
  if (window.__gNavArmed && NAV_SHORTCUTS[ev.key]) {
    ev.preventDefault();
    window.__gNavArmed = false;
    location.hash = NAV_SHORTCUTS[ev.key];
  }
});

document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
document.getElementById("palette-trigger").addEventListener("click", () => openPalette());

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
  const versionEl = document.getElementById("app-version");
  if (versionEl && r.version) versionEl.textContent = `payload v${r.version}`;
}).catch(() => {});

navigate();
