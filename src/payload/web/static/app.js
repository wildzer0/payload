/* payload web UI — vanilla JS, no external dependencies. Minimal
 * router on location.hash, a fetch() helper with uniform error
 * handling (the same JSON shape for every error, see web/errors.py),
 * and EventSource for the build-all live view. */
"use strict";

const COMMIT_MESSAGE_MAX_LENGTH = 1024;
const MAX_BUILD_ALL_JOBS = 32;
const HISTORY_PAGE_SIZE = 4;

/* ---------- theme ---------- */

(function initTheme() {
  const saved = localStorage.getItem("payload-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
})();

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const currentlyDark = current ? current === "dark" : systemDark;
  const next = currentlyDark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("payload-theme", next);
}

/* ---------- rendering utilities ---------- */

function escapeHtml(value) {
  const s = String(value);
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function raw(value) {
  // explicit marker: content is already safe HTML, must not be escaped
  return { __raw: String(value) };
}

function render(strings, ...values) {
  // An interpolated array is always a list of already-ready HTML
  // fragments (from .map(x => render`...`), or hand-written HTML
  // literals) — not user text to escape, which is why it does NOT go
  // through escapeHtml: always wrap it explicitly in raw() upstream if
  // an array of raw user strings is ever needed.
  return strings.reduce((out, s, i) => {
    const v = values[i];
    if (v === undefined) return out + s;
    if (Array.isArray(v)) return out + s + v.map((x) => (x && x.__raw !== undefined ? x.__raw : x)).join("");
    if (v && v.__raw !== undefined) return out + s + v.__raw;
    return out + s + escapeHtml(v);
  }, "");
}

/* ---------- icons (inline SVG, no icon-font/CDN) ---------- */

const ICONS = {
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 6 7 20 7"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/><path d="M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.4 6.4L3 16"/><path d="M3 21v-5h5"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h11l3 3v13H5V4Z"/><path d="M8 4v5h7V4"/><path d="M8 14h8v6H8v-6Z"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20L12 3Z"/><line x1="12" y1="9.5" x2="12" y2="14"/><circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 13 10 18 19 7"/></svg>',
  cross: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>',
  warnTri: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4 3 20h18L12 4Z"/><line x1="12" y1="10" x2="12" y2="14.5"/><circle cx="12" cy="17.2" r="0.4" fill="currentColor" stroke="none"/></svg>',
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="6" y1="12" x2="18" y2="12"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5c0-1 .8-1.5 2-1.5h6v15H6c-1.2 0-2 .5-2 1.5V5.5Z"/><path d="M20 5.5c0-1-.8-1.5-2-1.5h-6v15h6c1.2 0 2 .5 2 1.5V5.5Z"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6-5.9-3.3-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8Z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M4 19h16"/></svg>',
};

function iconSpan(name) {
  // plain HTML string (not raw()): for literal templates NOT tagged
  // with render (e.g. the pipeline builder, built with plain strings
  // due to its very dynamic/re-rendering nature).
  return `<span class="icon">${ICONS[name] || ""}</span>`;
}

function icon(name) {
  return raw(iconSpan(name));
}

/* ---------- toast (replaces the old single banner) ---------- */

function toast(message, kind, hint) {
  const stack = document.getElementById("toast-stack");
  const el = document.createElement("div");
  el.className = "toast toast-" + (kind || "error");
  el.innerHTML = render`
    <div class="toast-body">
      <div class="toast-message">${message}</div>
      ${raw(hint ? render`<div class="toast-hint">${hint}</div>` : "")}
    </div>
    <button class="toast-close" type="button" aria-label="Close">${icon("close")}</button>
  `;
  el.querySelector(".toast-close").onclick = () => el.remove();
  stack.appendChild(el);
  // every toast disappears on its own — error ones stay longer (more
  // text to read), the close button remains available to dismiss them
  // sooner regardless.
  const AUTO_DISMISS_MS = { ok: 4000, warn: 6000, error: 8000 };
  setTimeout(() => el.remove(), AUTO_DISMISS_MS[kind] || AUTO_DISMISS_MS.error);
}

function toastError(e) {
  toast(e.message || String(e), "error", e.hint);
}

/* ---------- confirmation modal (replaces native confirm()) ---------- */

function confirmDialog(message, opts) {
  opts = opts || {};
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  return new Promise((resolveFn) => {
    box.innerHTML = render`
      <p>${message}</p>
      <div class="modal-actions">
        <button type="button" id="modal-cancel">Cancel</button>
        <button type="button" class="${opts.danger ? "danger" : "primary"}" id="modal-confirm">${opts.confirmLabel || "Confirm"}</button>
      </div>
    `;
    overlay.hidden = false;
    const cleanup = (result) => { overlay.hidden = true; resolveFn(result); };
    box.querySelector("#modal-cancel").onclick = () => cleanup(false);
    box.querySelector("#modal-confirm").onclick = () => cleanup(true);
    overlay.onclick = (ev) => { if (ev.target === overlay) cleanup(false); };
  });
}

/* Same modal as confirmDialog, with a text field — used by import
 * (table/batch name to assign to the dropped file). Resolves to the
 * entered text, or null if cancelled/empty. */
function promptDialog(message, opts) {
  opts = opts || {};
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  return new Promise((resolveFn) => {
    box.innerHTML = render`
      <p>${message}</p>
      <div class="field"><input type="text" id="modal-prompt-input" value="${opts.value || ""}" placeholder="${opts.placeholder || ""}"></div>
      <div class="modal-actions">
        <button type="button" id="modal-cancel">Cancel</button>
        <button type="button" class="primary" id="modal-confirm">${opts.confirmLabel || "Confirm"}</button>
      </div>
    `;
    overlay.hidden = false;
    const input = box.querySelector("#modal-prompt-input");
    input.focus();
    input.select();
    const cleanup = (result) => { overlay.hidden = true; resolveFn(result); };
    box.querySelector("#modal-cancel").onclick = () => cleanup(null);
    box.querySelector("#modal-confirm").onclick = () => cleanup(input.value.trim() || null);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") cleanup(input.value.trim() || null); });
    overlay.onclick = (ev) => { if (ev.target === overlay) cleanup(null); };
  });
}

/* ---------- fetch API ---------- */

async function api(path, opts) {
  opts = opts || {};
  const headers = opts.body ? { "Content-Type": "application/json" } : {};
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new ApiError("Can't reach the server", "Is the 'pld serve' process still running?");
  }
  if (res.status === 204) return null;
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    throw new ApiError((data && data.message) || res.statusText, data && data.hint, data);
  }
  return data;
}

/* Like api(), but for multipart/form-data (file upload, see
 * /api/table/import) — no manual Content-Type: the browser generates
 * one with the correct boundary on its own when the body is a FormData. */
async function apiUpload(path, formData) {
  let res;
  try {
    res = await fetch(path, { method: "POST", body: formData });
  } catch (e) {
    throw new ApiError("Can't reach the server", "Is the 'pld serve' process still running?");
  }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    throw new ApiError((data && data.message) || res.statusText, data && data.hint, data);
  }
  return data;
}

class ApiError extends Error {
  constructor(message, hint, data) {
    super(message);
    this.hint = hint;
    this.data = data || null; // full JSON error body — used by the pipeline builder to read 'stage_index'
  }
}

function statusPill(status) {
  const map = {
    ok: ["pill-ok", "ok", "check"], match: ["pill-ok", "match", "check"], clean: ["pill-ok", "unchanged", "check"],
    warn: ["pill-warn", "warn", "warnTri"], dirty: ["pill-warn", "changed", "warnTri"], stale: ["pill-warn", "stale", "warnTri"],
    fail: ["pill-fail", "fail", "cross"], mismatch: ["pill-fail", "mismatch", "cross"], error: ["pill-fail", "error", "cross"],
    missing: ["pill-dim", "missing", "dash"], never_saved: ["pill-dim", "unsaved", "dash"],
    noop: ["pill-dim", "-", "dash"],
  };
  const [cls, label, iconName] = map[status] || ["pill-dim", status || "-", "dash"];
  return raw(`<span class="pill pill-status ${cls}">${ICONS[iconName] || ""}${escapeHtml(label)}</span>`);
}

function baseName(path) {
  const parts = String(path).split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return n + " B";
  return (n / 1024).toFixed(1) + " KB";
}

function debounce(fn, waitMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), waitMs);
  };
}

/* ---------- lightweight autocomplete (replaces <datalist>) ---------- */

/* Native <datalist> can't be styled (every browser/OS draws its own
 * popup, often clipping longer options) and clashed with the rest of
 * the interface — this is an absolutely positioned dropdown, filtered
 * by the typed value, with keyboard navigation, built with the same
 * CSS as everything else: no new dependency, just extra markup and
 * JS. 'input' must sit inside an element with class
 * 'autocomplete-wrap' (the dropdown anchors there). */
function attachAutocomplete(input, getOptions) {
  const wrap = input.closest(".autocomplete-wrap");
  if (!wrap) return; // defensive: without a wrapper there's nowhere to anchor the dropdown
  const list = document.createElement("div");
  list.className = "autocomplete-list";
  list.hidden = true;
  wrap.appendChild(list);

  let items = [];
  let activeIndex = -1;

  const setActive = (i) => {
    activeIndex = i;
    list.querySelectorAll(".autocomplete-item").forEach((el, idx) => el.classList.toggle("active", idx === i));
    const activeEl = list.querySelector(".autocomplete-item.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  };

  const renderList = () => {
    const q = input.value.trim().toLowerCase();
    const options = getOptions();
    items = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
    if (!items.length) { list.hidden = true; return; }
    list.innerHTML = items.map((o) => `<div class="autocomplete-item">${escapeHtml(o)}</div>`).join("");
    activeIndex = -1;
    list.hidden = false;
  };

  const close = () => { list.hidden = true; activeIndex = -1; };

  const select = (value) => {
    input.value = value;
    close();
    input.focus();
  };

  input.setAttribute("autocomplete", "off");
  input.addEventListener("input", renderList);
  input.addEventListener("focus", renderList);
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (ev) => {
    if (list.hidden) return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); setActive(Math.min(activeIndex + 1, items.length - 1)); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setActive(Math.max(activeIndex - 1, 0)); }
    else if (ev.key === "Enter" && activeIndex >= 0) { ev.preventDefault(); select(items[activeIndex]); }
    else if (ev.key === "Escape") { close(); }
  });
  // mousedown, not click: prevents the input's blur from firing before the click on the option arrives
  list.addEventListener("mousedown", (ev) => {
    const itemEl = ev.target.closest(".autocomplete-item");
    if (!itemEl) return;
    ev.preventDefault();
    select(items[Array.from(list.children).indexOf(itemEl)]);
  });
}

/* ---------- shared page helpers ---------- */

function pageHeader(title, subtitle, actionsHtml) {
  return render`<div class="page-header"><div class="page-header-text"><h1>${title}</h1>${raw(subtitle ? render`<p class="subtitle">${subtitle}</p>` : "")}</div>${raw(actionsHtml ? `<div class="page-header-actions">${actionsHtml}</div>` : "")}</div>`;
}

function skeletonLoading() {
  return `
    <div class="card">
      <div class="skeleton" style="width:180px;height:18px;margin-bottom:16px"></div>
      <div class="skeleton" style="width:100%;margin-bottom:8px"></div>
      <div class="skeleton" style="width:92%;margin-bottom:8px"></div>
      <div class="skeleton" style="width:70%"></div>
    </div>
  `;
}

function emptyCard(message, hint) {
  return render`<div class="card empty-state">${icon("alert")}<div>${message}</div>${raw(hint ? render`<div class="subtitle" style="margin-top:6px">${hint}</div>` : "")}</div>`;
}

/* Lightweight formatter for plugin docstrings: paragraphs separated by
 * a blank line, lines starting with "- "/"* " become a bullet list,
 * indented lines (syntax examples, like raw_text/csv's) become a
 * <pre> that preserves line breaks — not full markdown (that's for
 * the real guides, see renderMarkdown), just the minimum to not lose
 * the structure the backend already preserves via inspect.getdoc().
 *
 * Note: classification is per LINE, not per block separated by a
 * blank line — a line like "Example:" often precedes the actual
 * example with no blank line in between (see RawTextReader/CsvReader),
 * so an "all or nothing" block would treat the whole paragraph as
 * prose and would still lose the example's line breaks. */
function formatDescription(text) {
  if (!text || !text.trim()) {
    return '<p class="empty-state" style="padding:12px 0">This plugin doesn\'t provide a description.</p>';
  }
  const allLines = text.replace(/\r\n/g, "\n").split("\n");
  while (allLines.length && allLines[0].trim() === "") allLines.shift();
  while (allLines.length && allLines[allLines.length - 1].trim() === "") allLines.pop();

  const groups = [];
  let current = null;
  for (const line of allLines) {
    if (line.trim() === "") {
      if (current) current.lines.push(line);
      continue;
    }
    const type = /^[ \t]/.test(line) ? "pre" : "prose";
    if (!current || current.type !== type) {
      current = { type, lines: [] };
      groups.push(current);
    }
    current.lines.push(line);
  }

  return groups.map((g) => {
    if (g.type === "pre") {
      const nonBlank = g.lines.filter((l) => l.trim() !== "");
      const minIndent = Math.min(...nonBlank.map((l) => l.match(/^[ \t]*/)[0].length));
      const dedented = g.lines.map((l) => (l.trim() === "" ? "" : l.slice(minIndent))).join("\n").trim();
      return `<pre class="doc-example">${escapeHtml(dedented)}</pre>`;
    }
    const paragraphs = g.lines.join("\n").split(/\n\s*\n/);
    return paragraphs.map((p) => {
      const lines = p.split("\n").map((l) => l.trim()).filter(Boolean);
      if (!lines.length) return "";
      const isList = lines.every((l) => /^[-*]\s+/.test(l));
      if (isList) {
        return `<ul>${lines.map((l) => `<li>${escapeHtml(l.replace(/^[-*]\s+/, ""))}</li>`).join("")}</ul>`;
      }
      return `<p>${lines.map(escapeHtml).join(" ")}</p>`;
    }).join("");
  }).join("");
}

function metaChip(label, value) {
  return `<span class="meta-chip"><strong>${escapeHtml(label)}</strong><span class="mono">${escapeHtml(value)}</span></span>`;
}

/* Collapsible card (<details>): used for secondary or config sections
 * — closed by default by passing open:false, so the user decides what
 * to see instead of being met with a full page. */
function detailsCard(title, bodyHtml, opts) {
  opts = opts || {};
  const open = opts.open !== false;
  return `
    <details class="card" ${open ? "open" : ""}>
      <summary><h2>${escapeHtml(title)}</h2><span class="icon chevron">${ICONS.down}</span></summary>
      <div class="details-body">${bodyHtml}</div>
    </details>
  `;
}

/* Always-open, non-collapsible card — used for History on the table
 * page: it needs to stay clearly visible, not hidden behind a click
 * like the other secondary sections. */
function pinnedCard(title, bodyHtml) {
  return `
    <div class="card">
      <h2 style="margin:0 0 16px">${escapeHtml(title)}</h2>
      ${bodyHtml}
    </div>
  `;
}

/* ---------- router ---------- */

const ROUTES = [
  [/^\/$/, viewDashboard],
  [/^\/table\/([^/]+)$/, viewTable],
  [/^\/build-all$/, viewBuildAll],
  [/^\/plugins$/, viewPlugins],
  [/^\/plugin\/([^/]+)$/, viewPluginDetail],
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

/* ---------- dashboard ---------- */

/* Default reader/writer <select> for a dashboard row: the 'auto'
 * option shows in parentheses what would actually be resolved today
 * (resolvedValue), the other options are the explicit override — a
 * single control covers both "what happens" and "what I chose".
 * Disabled (with a separate badge in the table) if the table has an
 * explicit pipeline: in that case default reader/writer don't apply
 * at all, they're ignored by resolution. */
/* Compact format for the dashboard's 'Last modified' column: the full
 * ISO string ("2026-08-01T00:21:36") is too long for a narrow column
 * and used to wrap, breaking the row height — here just
 * day/month/2-digit-year + hour:minutes, the full timestamp remains
 * available on hover via title. */
function fmtShortTimestamp(iso) {
  if (!iso) return "—";
  const [datePart, timePart] = iso.split("T");
  const [y, m, d] = datePart.split("-");
  const hm = (timePart || "").slice(0, 5);
  return `${d}/${m}/${y.slice(2)} ${hm}`;
}

function _defaultSelectHtml(kind, tableName, options, currentValue, resolvedValue, disabled) {
  const id = `dd-${kind}-${tableName}`;
  const autoLabel = disabled ? "pipeline" : (resolvedValue ? `auto (${resolvedValue})` : "auto");
  const opts = [`<option value="">${escapeHtml(autoLabel)}</option>`].concat(
    options.map((o) => `<option value="${escapeHtml(o)}"${o === currentValue ? " selected" : ""}>${escapeHtml(o)}</option>`)
  );
  return `<select id="${id}" class="inline-select" data-default-kind="${kind}" data-default-table="${escapeHtml(tableName)}"${disabled ? " disabled" : ""}>${opts.join("")}</select>`;
}

/* Highlights the "current" snapshot (accent, same treatment as the
 * table page) and, if the history HEAD is further ahead (the table is
 * stuck at an earlier restore), flags it with a secondary chip —
 * otherwise the fact that more recent, never-"reactivated" snapshots
 * exist would disappear. Both the empty state and the "behind HEAD"
 * state are kept to short, non-wrapping content (icon + at most a
 * number) with the explanation moved into the title tooltip, since
 * this chip lives in a narrow grid cell where multi-word labels wrap
 * mid-phrase and look broken. */
function _snapshotChipHtml(t) {
  if (!t.last_snapshot) {
    return `<span class="pill pill-dim" title="No snapshot saved yet">${iconSpan("dash")}—</span>`;
  }
  const current = `<span class="pill pill-current">#${t.last_snapshot.id}</span>`;
  const behindTip = t.tip_snapshot_id && t.tip_snapshot_id !== t.last_snapshot.id;
  const tipNote = behindTip
    ? `<span class="pill pill-dim" title="History goes up to snapshot #${t.tip_snapshot_id}, this table is showing an earlier restore">${iconSpan("warnTri")}#${t.tip_snapshot_id}</span>`
    : "";
  return current + tipNote;
}

/* A card per table instead of a row in one big HTML table: with 9
 * columns of different information (status, golden, pipeline,
 * sidecar, reader, writer, size, date, snapshot) a rigid table forced
 * every cell into a fixed width and content wrapped, breaking
 * alignment — here every piece of information is a chip that lays
 * itself out (and wraps) on its own via flexbox, without ever
 * breaking the alignment of the rows above/below because there are no
 * columns shared between cards. */
function _importZoneHtml() {
  return `
    <div class="card import-zone">
      <h2>Import table</h2>
      <p class="subtitle">Drag one or more files here to copy them into the project — the tool decides where, no more organizing folders by hand. Multiple files together become a batch table.</p>
      <label class="import-drop" id="import-drop" for="import-file-input">
        ${iconSpan("box")}
        <span>Drag here, or click to choose one or more files</span>
      </label>
      <input type="file" id="import-file-input" multiple hidden>
    </div>`;
}

function _orphanedTablesHtml(names) {
  if (!names.length) return "";
  const rows = names.map((n) => `
    <div class="orphaned-table-row">
      <span class="mono">${escapeHtml(n)}</span>
      <button data-restore-orphan="${escapeHtml(n)}">${iconSpan("refresh")}Restore</button>
    </div>`).join("");
  return `
    <div class="card">
      <h2>Deleted but restorable tables</h2>
      <p class="subtitle">These still have saved history but are no longer on disk (deleted with 'pld rm', the web action, or by hand) — 'Restore' brings them back to the last snapshot.</p>
      <div class="local-plugin-list">${rows}</div>
    </div>`;
}

async function viewDashboard() {
  const [report, status, plugins, tracked, health] = await Promise.all([
    api("/api/report"), api("/api/status"), getPlugins(), api("/api/log"), api("/api/health"),
  ]);
  const stateByName = Object.fromEntries(status.tables.map((t) => [t.name, t.state]));
  const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
  const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);
  const liveNames = new Set(report.tables.map((t) => t.name));
  const orphanedNames = tracked.tables.filter((n) => !liveNames.has(n));

  const pathByName = Object.fromEntries(status.tables.map((t) => [t.name, t.path]));
  const total = report.tables.length;
  const synced = report.tables.filter((t) => stateByName[t.name] === "clean").length;
  const mismatches = report.tables.filter((t) => t.golden_status === "mismatch" || t.golden_status === "stale").length;
  const dirty = report.tables.filter((t) => stateByName[t.name] === "dirty").length;

  const cards = report.tables.map((t) => render`
    <div class="table-summary-card">
      <div class="table-summary-head">
        <a class="link table-summary-name" href="#/table/${t.name}">${t.name}</a>
        <div class="table-summary-actions">
          <div class="table-summary-badges">
            ${statusPill(stateByName[t.name] || "never_saved")}
            ${statusPill(t.golden_status || "missing")}
            ${raw(t.golden_snapshot_id ? goldBadge() : "")}
            ${raw(t.pipeline_explicit ? '<span class="pill pill-warn" title="Explicit pipeline configured: overrides default reader/writer">pipeline</span>' : "")}
            ${raw(t.has_sidecar ? '<span class="pill pill-dim" title="Sidecar (<name>.config.toml) active for this table">override</span>' : "")}
          </div>
          ${raw(t.output_size != null
            ? `<a class="btn icon-only" href="/api/table/${encodeURIComponent(t.name)}/download" title="Download the last built output" download>${iconSpan("download")}</a>`
            : "")}
          <button class="icon-only" data-quick-build="${t.name}" title="Quick build (uses default reader/writer, no other parameter)">${icon("play")}</button>
        </div>
      </div>
      <div class="table-summary-meta">
        <span class="meta-chip meta-chip-control"><strong>Reader</strong>${raw(_defaultSelectHtml("reader", t.name, readerNames, t.reader_override, t.resolved_reader, t.pipeline_explicit))}</span>
        <span class="meta-chip meta-chip-control"><strong>Writer</strong>${raw(_defaultSelectHtml("writer", t.name, writerNames, t.writer_override, t.resolved_writer, t.pipeline_explicit))}</span>
        <span class="meta-chip"><strong>Size</strong><span class="mono">${fmtBytes(t.source_size)} → ${fmtBytes(t.output_size)}</span></span>
        <span class="meta-chip" title="${t.source_mtime}"><strong>Modified</strong><span class="mono">${fmtShortTimestamp(t.source_mtime)}</span></span>
        <span class="meta-chip"><strong>Snapshot</strong>${raw(_snapshotChipHtml(t))}</span>
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader(health.project_name, `Dashboard · ${total} tables discovered in this project.`))}
    ${raw(_importZoneHtml())}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Total tables</div><div class="stat-value">${total}</div></div>
      <div class="stat-card"><div class="stat-label">Synced</div><div class="stat-value">${synced}</div></div>
      <div class="stat-card ${mismatches ? "stat-fail" : ""}"><div class="stat-label">Golden mismatch/stale</div><div class="stat-value">${mismatches}</div></div>
      <div class="stat-card ${dirty ? "stat-warn" : ""}"><div class="stat-label">To save</div><div class="stat-value">${dirty}</div></div>
    </div>
    <div class="table-summary-list">${cards.length ? cards : ['<p class="empty-state card">No table found.</p>']}</div>
    ${raw(_orphanedTablesHtml(orphanedNames))}
  `;

  const dropZone = document.getElementById("import-drop");
  const importFileInput = document.getElementById("import-file-input");
  ["dragenter", "dragover"].forEach((evt) => dropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    dropZone.classList.add("import-drop-active");
  }));
  ["dragleave", "drop"].forEach((evt) => dropZone.addEventListener(evt, (ev) => {
    ev.preventDefault();
    dropZone.classList.remove("import-drop-active");
  }));
  dropZone.addEventListener("drop", (ev) => _handleImportFiles(ev.dataTransfer.files));
  importFileInput.addEventListener("change", () => {
    _handleImportFiles(importFileInput.files);
    importFileInput.value = "";
  });

  document.querySelectorAll("[data-restore-orphan]").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.restoreOrphan;
      btn.disabled = true;
      try {
        const preview = await api("/api/restore", { body: { table_name: name } });
        const batchNote = preview.recreate_batch_entry ? " Its [[batch_table]] entry will also be re-added to table-tool.toml." : "";
        const ok = await confirmDialog(`'${name}' will be recreated from the last snapshot (#${preview.snapshot_id}).${batchNote}`, { confirmLabel: "Restore" });
        if (!ok) { btn.disabled = false; return; }
        const r = await api("/api/restore", { body: { table_name: name, snapshot_id: preview.snapshot_id, confirm: true } });
        toast(`'${name}' restored`, "ok");
        if (r.pipeline_warning) toast(r.pipeline_warning, "warn");
        viewDashboard();
      } catch (e) {
        toastError(e);
        btn.disabled = false;
      }
    };
  });

  document.querySelectorAll("[data-quick-build]").forEach((btn) => {
    btn.onclick = async () => {
      const table = btn.dataset.quickBuild;
      btn.disabled = true;
      try {
        const r = await api("/api/build", { body: { source: pathByName[table] } });
        toast(`Build of '${table}' completed: ${r.outputs.join(", ")} (${r.was_built ? "rebuilt" : "from cache"})`, "ok");
        viewDashboard();
      } catch (e) {
        toastError(e);
        btn.disabled = false;
      }
    };
  });

  document.querySelectorAll("[data-default-kind]").forEach((sel) => {
    sel.onchange = async () => {
      const kind = sel.dataset.defaultKind;
      const table = sel.dataset.defaultTable;
      sel.disabled = true;
      try {
        const current = await api("/api/sidecar/" + encodeURIComponent(table));
        const defaults = { ...(current.defaults || {}) };
        if (sel.value) defaults[kind] = sel.value; else delete defaults[kind];
        await api("/api/sidecar/" + encodeURIComponent(table), { method: "PUT", body: { defaults } });

        if (kind === "reader" && sel.value) {
          // the chosen reader exists, but does it REALLY read this
          // table's file? same check as 'Validate conformance' on
          // plugins, here pointed at the reader just set as default.
          try {
            const v = await api("/api/source/" + encodeURIComponent(table) + "/validate", { method: "POST" });
            if (v.conforms) {
              toast(`Default reader for '${table}' updated: reads the file correctly`, "ok");
            } else {
              toast(`Reader set, but '${v.reader}' can't read the source of '${table}'`, "warn", v.issues.map((i) => i.detail).join("; "));
            }
          } catch (ve) {
            toast(`Default reader for '${table}' updated (verification failed: ${ve.message})`, "warn");
          }
        } else {
          toast(`Default ${kind === "reader" ? "reader" : "writer"} for '${table}' updated`, "ok");
        }
        viewDashboard();
      } catch (e) {
        toastError(e);
        sel.disabled = false;
      }
    };
  });
}

/* One or more files dragged/chosen in the dashboard's drop zone — a
 * single file asks for the table name (default: filename without
 * extension), multiple files together ask for the name of the new
 * batch table that will contain all of them (same choice as the CLI:
 * 'pld import <path> [--as name]' vs 'pld import <path...> --new-batch
 * name', see cli.py import_cmd). A name that already exists offers to
 * overwrite instead of silently refusing. */
async function _handleImportFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;

  const formData = new FormData();
  if (files.length === 1) {
    const defaultName = files[0].name.replace(/\.[^.]+$/, "");
    const name = await promptDialog(`Table name for '${files[0].name}'?`, { value: defaultName, confirmLabel: "Import" });
    if (name === null) return;
    formData.append("file", files[0]);
    if (name !== defaultName) formData.append("as_name", name);
  } else {
    const batchName = await promptDialog(`Name of the new batch table for these ${files.length} files?`, { placeholder: "batch_name", confirmLabel: "Import" });
    if (!batchName) return;
    files.forEach((f) => formData.append("file", f));
    formData.append("new_batch", batchName);
  }

  try {
    const r = await apiUpload("/api/table/import", formData);
    toast(`Import completed: ${r.kind === "batch" ? r.name : r.path}`, "ok");
    viewDashboard();
  } catch (e) {
    if (e.data && e.data.error === "TableAlreadyExistsError" && files.length === 1) {
      const ok = await confirmDialog(`${e.message}. Do you want to overwrite its content?`, { danger: true, confirmLabel: "Overwrite" });
      if (!ok) return;
      formData.append("overwrite", "true");
      try {
        const r2 = await apiUpload("/api/table/import", formData);
        toast(`'${r2.path}' updated`, "ok");
        viewDashboard();
      } catch (e2) {
        toastError(e2);
      }
      return;
    }
    toastError(e);
  }
}

/* ---------- table detail ---------- */

async function viewTable(name) {
  const content = document.getElementById("content");

  const buildBody = `
    <div class="field-row">
      <div class="field"><label>Writer (--to)</label><div class="autocomplete-wrap"><input type="text" id="f-to" placeholder="bin"></div></div>
      <div class="field"><label>Reader (--from)</label><div class="autocomplete-wrap"><input type="text" id="f-from" placeholder="auto"></div></div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="f-force"><span>--force</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-dry"><span>--dry-run</span></label>
      <label class="toggle-chip"><input type="checkbox" id="f-golden"><span>--check-golden</span></label>
    </div>
    <div class="build-actions">
      <button class="primary" id="btn-build">${iconSpan("play")}Build</button>
    </div>
    <div id="build-result"></div>
  `;

  const historyBody = `
    <div id="golden-summary" style="margin-bottom:14px"></div>
    <div class="field">
      <label>Commit message</label>
      <textarea id="commit-message" class="commit-message-input mono" rows="3" maxlength="${COMMIT_MESSAGE_MAX_LENGTH}" placeholder="Describe what changed…"></textarea>
      <div class="field-hint"><span id="commit-message-count">0</span>/${COMMIT_MESSAGE_MAX_LENGTH}</div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="commit-golden"><span>${iconSpan("star")}Also set as golden</span></label>
    </div>
    <div class="build-actions">
      <button id="btn-commit">${iconSpan("save")}Commit changes</button>
    </div>
    <div id="history-result"></div>
  `;

  const headerActionsHtml = `
    <a class="btn icon-only" id="btn-download-output" href="/api/table/${encodeURIComponent(name)}/download" title="Download the last built output" download hidden>${iconSpan("download")}</a>
    <button class="danger" id="btn-delete-table">${iconSpan("trash")}Delete table</button>
  `;

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/">← Dashboard</a></div>
    ${raw(pageHeader(name, undefined, headerActionsHtml))}
    <div class="table-detail-layout">
      <div class="table-detail-main">
        ${raw(detailsCard("Build", buildBody, { open: true }))}
        ${raw(detailsCard("Source content", '<div id="view-result"><p class="empty-state">—</p></div>', { open: false }))}
        ${raw(detailsCard("Pipeline", '<div id="pipeline-result"></div>', { open: true }))}
        ${raw(detailsCard("Table-specific config (sidecar)", '<div id="sidecar-result"></div>', { open: false }))}
      </div>
      <div class="table-detail-side">
        ${raw(pinnedCard("History", historyBody))}
      </div>
    </div>
  `;

  getPlugins().then((plugins) => {
    const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
    const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);
    attachAutocomplete(document.getElementById("f-from"), () => readerNames);
    attachAutocomplete(document.getElementById("f-to"), () => writerNames);
  }).catch(() => {});

  // The --from/--to placeholders reflect the default reader/writer
  // already set for this table (dashboard or sidecar) — leaving the
  // field empty will really use that default, writing something in it
  // only overrides it for THIS build, without touching the saved default.
  api("/api/report").then((report) => {
    const row = report.tables.find((t) => t.name === name);
    if (!row) return;
    if (row.output_size != null) document.getElementById("btn-download-output").hidden = false;
    if (row.pipeline_explicit) return;
    if (row.resolved_reader) document.getElementById("f-from").placeholder = row.resolved_reader;
    if (row.resolved_writer) document.getElementById("f-to").placeholder = row.resolved_writer;
  }).catch(() => {});

  document.getElementById("btn-build").onclick = async () => {
    const body = {
      source: findSourcePath(name), to: val("f-to") || undefined, from: val("f-from") || undefined,
      force: chk("f-force"), dry_run: chk("f-dry"), check_golden: chk("f-golden"),
    };
    try {
      const r = await api("/api/build", { body });
      const status = r.dry_run
        ? (r.was_built ? "dry-run: would be rebuilt, no file written" : "dry-run: would use the cache, no file written")
        : (r.was_built ? "rebuilt" : "from cache");
      if (!r.dry_run) document.getElementById("btn-download-output").hidden = false;
      toast(`Build ok: ${r.outputs.join(", ")} (${status})`, "ok");
      loadPipelineBuilder(name);
    } catch (e) {
      toastError(e);
    }
  };

  const commitMessageEl = document.getElementById("commit-message");
  const commitMessageCountEl = document.getElementById("commit-message-count");
  commitMessageEl.addEventListener("input", () => { commitMessageCountEl.textContent = commitMessageEl.value.length; });

  document.getElementById("btn-commit").onclick = async () => {
    const message = val("commit-message") || `from web UI: ${name}`;
    const setAsGolden = chk("commit-golden");
    try {
      const r = await api("/api/commit", { body: { message, only: [name] } });
      if (!r.committed.length) {
        toast("Nothing to save", "ok");
        return;
      }
      const snapshotId = r.committed[0].snapshot_id;
      const missing = r.committed[0].missing_outputs || [];
      if (setAsGolden) {
        await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
      }
      if (missing.length) {
        toast(
          `Snapshot #${snapshotId} saved, but the pipeline is incomplete${setAsGolden ? " (★ golden)" : ""}`,
          "warn",
          `Missing: ${missing.join(", ")} — a writer in the group produced no output`
        );
      } else {
        toast(`Snapshot #${snapshotId} saved${setAsGolden ? " (★ golden)" : ""}`, "ok");
      }
      commitMessageEl.value = "";
      commitMessageCountEl.textContent = "0";
      document.getElementById("commit-golden").checked = false;
      loadHistory(name);
      loadGoldenSummary(name);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("btn-delete-table").onclick = async () => {
    try {
      const preview = await api("/api/table/delete", { body: { table_name: name } });
      const lines = [`The following will be deleted: ${preview.sources.join(", ")} and its output.`];
      lines.push("History stays intact and browsable in 'History' — you can restore it from the Dashboard.");
      if (preview.is_batch) lines.push("Its [[batch_table]] will also be removed from table-tool.toml.");
      if (preview.dirty) lines.push("⚠ This table has unsaved changes: they will be lost forever.");
      const ok = await confirmDialog(lines.join(" "), { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      await api("/api/table/delete", { body: { table_name: name, confirm: true } });
      toast(`'${name}' deleted (source + output)`, "ok");
      location.hash = "#/";
    } catch (e) {
      toastError(e);
    }
  };

  await Promise.all([
    ensureTableSources(), loadSource(name), loadPipelineBuilder(name),
    loadSidecarCard(name), loadHistory(name), loadGoldenSummary(name),
  ]);
}

let _tableSources = null;
function findSourcePath(name) {
  return (_tableSources && _tableSources[name]) || name;
}
/* Populates _tableSources (table name -> absolute path) ONCE per page,
 * BEFORE any handler might need it (Build first and foremost) — must
 * be called from viewTable() itself, not just from the branches that
 * happen to go through /api/status (e.g. the hex fallback for
 * non-text sources): otherwise Build on an editable table (the common
 * case) would use just the table name instead of the path, and the
 * backend would respond 'file not found'. */
async function ensureTableSources() {
  if (_tableSources) return;
  const status = await api("/api/status");
  _tableSources = Object.fromEntries(status.tables.map((t) => [t.name, t.path]));
}

let _pluginsCache = null;
async function getPlugins() {
  if (!_pluginsCache) _pluginsCache = await api("/api/plugins");
  return _pluginsCache;
}

/* Must be invalidated every time a local plugin is created, saved, or
 * deleted: without this, a just-added plugin would stay invisible
 * (dashboard, reader/writer selects, etc.) until the page is manually
 * reloaded — the router re-fetches on every navigation anyway, this
 * just has to stop serving stale data. */
function invalidatePluginsCache() {
  _pluginsCache = null;
}

async function hexDumpHtml(name) {
  await ensureTableSources();
  const ir = await api("/api/view?source=" + encodeURIComponent(findSourcePath(name)));
  const bytes = atob(ir.data_base64);
  const hexLines = [];
  for (let i = 0; i < bytes.length; i += 8) {
    const chunk = bytes.slice(i, i + 8);
    const hex = Array.from(chunk).map((c) => c.charCodeAt(0).toString(16).padStart(2, "0").toUpperCase()).join(" ");
    const comment = (ir.comments.find((c) => c.offset === i) || {}).text || "";
    hexLines.push(render`<div class="hex-chunk"><span class="offset">0x${i.toString(16).padStart(4, "0").toUpperCase()}</span><span>${hex}</span><span style="color:var(--text-dim)">${comment}</span></div>`);
  }
  return `<div class="log">${hexLines.join("") || '<span class="log-empty">empty</span>'}</div>`;
}

/* Source content editor directly on the page: text formats only (CSV,
 * raw text, C, ...) — a file that doesn't decode as UTF-8 (binary
 * blob) stays read-only, shown as hex like before, with a warning
 * instead of an editor that would corrupt it on first save. */
async function loadSource(name) {
  const el = document.getElementById("view-result");
  try {
    const info = await api("/api/source/" + encodeURIComponent(name));
    if (info.editable) {
      el.innerHTML = render`
        <textarea id="source-editor" class="source-editor mono" spellcheck="false" rows="14">${info.content}</textarea>
        <div class="source-editor-actions">
          <button class="primary" id="btn-save-source">${icon("save")}Save source</button>
          <button id="btn-validate-source">${icon("check")}Validate with default reader</button>
          <span class="subtitle mono">${info.path}</span>
        </div>
        <div id="source-validate-result"></div>
      `;
      const runValidate = async () => {
        const resultEl = document.getElementById("source-validate-result");
        try {
          const r = await api("/api/source/" + encodeURIComponent(name) + "/validate", { method: "POST" });
          if (r.conforms) {
            resultEl.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforms to reader '${r.reader}'</span></div>`;
          } else {
            const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
            resultEl.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>doesn't conform to reader '${r.reader}'</span><ul>${items}</ul></div>`;
          }
        } catch (e) {
          toastError(e);
        }
      };
      document.getElementById("btn-save-source").onclick = async () => {
        try {
          await api("/api/source/" + encodeURIComponent(name), { method: "PUT", body: { content: document.getElementById("source-editor").value } });
          toast("Source saved", "ok");
          runValidate();
        } catch (e) {
          toastError(e);
        }
      };
      document.getElementById("btn-validate-source").onclick = runValidate;
    } else {
      const hex = await hexDumpHtml(name);
      el.innerHTML = render`
        <div class="result-line">${statusPill("warn")}<span>${info.reason} — not editable from here, hex view only.</span></div>
        ${raw(hex)}
      `;
    }
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

function _stageToRawJs(stage) {
  if (stage.type === "reader" || stage.type === "writer") return { type: stage.type, name: stage.name };
  const out = { type: "exec", command: stage.command, on_error: stage.on_error || "fail" };
  if (stage.output_extension) out.output_extension = stage.output_extension;
  return out;
}

/* Visual pipeline builder: shows the current resolution (implicit from
 * --from/--to, or explicit from sidecar) as an editable stage list —
 * reordering/adding/removing client-side, but NO alternation rule
 * duplicated here: 'Save' sends the raw list to PUT
 * /api/pipeline/{table}, which validates with the same core
 * PipelineSpec.from_raw_stages() and responds with 'stage_index' on
 * error — that's what highlights the offending card. */
async function loadPipelineBuilder(name) {
  const el = document.getElementById("pipeline-result");
  try {
    const [p, plugins] = await Promise.all([api("/api/pipeline/" + encodeURIComponent(name)), getPlugins()]);
    const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
    const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);

    const stages = p.stages.map((s) => (
      s.kind === "exec"
        ? { type: "exec", command: s.command, on_error: s.on_error, output_extension: "" }
        : { type: s.kind, name: s.name }
    ));
    let lastError = null;

    function optionList(names, selected) {
      return names.map((n) => `<option value="${escapeHtml(n)}" ${n === selected ? "selected" : ""}>${escapeHtml(n)}</option>`).join("");
    }

    function renderStages() {
      const cards = stages.map((s, i) => {
        const badgeCls = s.type === "reader" ? "badge-reader" : s.type === "writer" ? "badge-writer" : "";
        const hasError = lastError && lastError.stage_index === i;
        let fields;
        if (s.type === "reader" || s.type === "writer") {
          const names = s.type === "reader" ? readerNames : writerNames;
          fields = `<select data-field="name" data-idx="${i}">${optionList(names, s.name)}</select>`;
        } else {
          fields = `
            <input type="text" data-field="command" data-idx="${i}" value="${escapeHtml(s.command || "")}" placeholder="external command, e.g. objcopy {input} {output}" style="flex:1;min-width:220px">
            <select data-field="on_error" data-idx="${i}">
              <option value="fail" ${s.on_error !== "warn" ? "selected" : ""}>on_error: fail</option>
              <option value="warn" ${s.on_error === "warn" ? "selected" : ""}>on_error: warn</option>
            </select>
            <input type="text" data-field="output_extension" data-idx="${i}" value="${escapeHtml(s.output_extension || "")}" placeholder="extension if final, e.g. .signed.bin" style="width:200px">
          `;
        }
        return `
          <div class="stage-card ${hasError ? "stage-error" : ""}">
            <span class="stage-badge ${badgeCls}">${s.type}</span>
            <div class="stage-fields">${fields}</div>
            <div class="stage-actions">
              <button type="button" class="icon-only ghost" data-move="up" data-idx="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">${iconSpan("up")}</button>
              <button type="button" class="icon-only ghost" data-move="down" data-idx="${i}" ${i === stages.length - 1 ? "disabled" : ""} aria-label="Move down">${iconSpan("down")}</button>
              <button type="button" class="icon-only ghost danger" data-remove="${i}" aria-label="Remove">${iconSpan("trash")}</button>
            </div>
          </div>
          ${hasError ? `<div class="stage-error-msg">${escapeHtml(lastError.message)}</div>` : ""}
        `;
      }).join("");

      el.innerHTML = `
        <div class="stage-list">${cards || '<p class="empty-state">No stage — add one to get started.</p>'}</div>
        <div class="add-stage-row">
          <select id="pb-add-type">
            <option value="reader">reader</option>
            <option value="writer">writer</option>
            <option value="exec">exec</option>
          </select>
          <button type="button" id="pb-add"><span class="icon">${ICONS.plus}</span>Add stage</button>
          <span style="flex:1"></span>
          <button type="button" id="pb-reset">${iconSpan("refresh")}Restore implicit</button>
          <button type="button" class="primary" id="pb-save">${iconSpan("save")}Save pipeline</button>
        </div>
        <div class="pipeline-output-row">
          <span class="pipeline-output-label">Output</span>
          ${p.outputs.length
            ? p.outputs.map((o) => `<span class="pill pill-dim mono" title="${escapeHtml(o)}">${escapeHtml(baseName(o))}</span>`).join("")
            : '<span class="subtitle">—</span>'}
          ${p.explicit ? '<span class="pill pill-warn">explicit (sidecar)</span>' : '<span class="pill pill-dim">automatic</span>'}
        </div>
      `;

      // Every local change invalidates the last save attempt's error:
      // both because stage indices might have changed (the highlight
      // would land on the wrong stage), and because the user may have
      // already fixed the problem — the error signal must disappear
      // right away, not stay stuck until Save is pressed again.
      el.querySelectorAll("[data-move]").forEach((btn) => {
        btn.onclick = () => {
          const i = Number(btn.getAttribute("data-idx"));
          const j = i + (btn.getAttribute("data-move") === "up" ? -1 : 1);
          if (j < 0 || j >= stages.length) return;
          const tmp = stages[i]; stages[i] = stages[j]; stages[j] = tmp;
          lastError = null;
          renderStages();
        };
      });
      el.querySelectorAll("[data-remove]").forEach((btn) => {
        btn.onclick = () => {
          stages.splice(Number(btn.getAttribute("data-remove")), 1);
          lastError = null;
          renderStages();
        };
      });
      el.querySelectorAll("[data-field]").forEach((input) => {
        input.onchange = () => {
          stages[Number(input.getAttribute("data-idx"))][input.getAttribute("data-field")] = input.value;
          lastError = null;
          renderStages();
        };
      });

      document.getElementById("pb-add").onclick = () => {
        const type = document.getElementById("pb-add-type").value;
        if (type === "exec") {
          stages.push({ type: "exec", command: "", on_error: "fail", output_extension: "" });
        } else {
          const names = type === "reader" ? readerNames : writerNames;
          stages.push({ type, name: names[0] || "" });
        }
        lastError = null;
        renderStages();
      };

      document.getElementById("pb-save").onclick = async () => {
        try {
          await api("/api/pipeline/" + encodeURIComponent(name), { method: "PUT", body: { stages: stages.map(_stageToRawJs) } });
          toast("Pipeline saved", "ok");
          loadPipelineBuilder(name);
        } catch (e) {
          lastError = { stage_index: e.data && typeof e.data.stage_index === "number" ? e.data.stage_index : -1, message: e.message };
          renderStages();
          toastError(e);
        }
      };

      document.getElementById("pb-reset").onclick = async () => {
        const ok = await confirmDialog("Go back to automatic resolution from --from/--to? The explicit pipeline saved in the sidecar will be removed.", { danger: true, confirmLabel: "Restore" });
        if (!ok) return;
        try {
          await api("/api/pipeline/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Pipeline restored to automatic resolution", "ok");
          loadPipelineBuilder(name);
        } catch (e) {
          toastError(e);
        }
      };
    }

    renderStages();
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

/* Sidecar card: one switch per field (schema shared with the global
 * config, see /api/config 'schema') — only fields with the switch on
 * end up in the PUT, consistent with core/config.py's "the sidecar
 * only overrides the keys it declares" model. */
async function loadSidecarCard(name) {
  const el = document.getElementById("sidecar-result");
  try {
    const [sidecar, cfg] = await Promise.all([api("/api/sidecar/" + encodeURIComponent(name)), api("/api/config")]);
    const schema = cfg.schema;
    const sidecarDefaults = sidecar.defaults || {};
    const sidecarToolchain = sidecar.toolchain || {};

    function row(section, f, current) {
      const has = Object.prototype.hasOwnProperty.call(current, f.key);
      const id = `sc-${section}-${f.key}`;
      const value = has ? current[f.key] : undefined;
      let inputHtml;
      if (f.type === "list") {
        inputHtml = `<input type="text" id="${id}" ${has ? "" : "disabled"} value="${escapeHtml((value || []).join(", "))}" placeholder="comma-separated">`;
      } else if (f.key === "byte_order") {
        inputHtml = `<select id="${id}" ${has ? "" : "disabled"}><option value="little" ${value !== "big" ? "selected" : ""}>little</option><option value="big" ${value === "big" ? "selected" : ""}>big</option></select>`;
      } else {
        inputHtml = `<input type="text" id="${id}" ${has ? "" : "disabled"} value="${escapeHtml(value === undefined ? "" : value)}">`;
      }
      return `<div class="field-with-toggle"><input type="checkbox" id="${id}-toggle" ${has ? "checked" : ""} data-toggle-for="${id}"><div class="field"><label>${escapeHtml(f.key)}</label>${inputHtml}</div></div>`;
    }

    const defaultsRows = schema.defaults.map((f) => row("defaults", f, sidecarDefaults)).join("");
    const toolchainRows = schema.toolchain.map((f) => row("toolchain", f, sidecarToolchain)).join("");
    const hasSidecar = Object.keys(sidecar).length > 0;

    el.innerHTML = `
      <p class="subtitle">Only the selected fields override the global config for this table.</p>
      <h2 style="margin-top:16px">Defaults</h2>
      ${defaultsRows}
      <h2>Toolchain</h2>
      ${toolchainRows}
      <div class="toolbar" style="margin-top:14px">
        <button type="button" class="primary" id="sc-save">${iconSpan("save")}Save sidecar</button>
        <button type="button" class="danger" id="sc-delete" ${hasSidecar ? "" : "disabled"}>${iconSpan("trash")}Delete sidecar</button>
      </div>
    `;

    el.querySelectorAll("[data-toggle-for]").forEach((cb) => {
      cb.onchange = () => { document.getElementById(cb.getAttribute("data-toggle-for")).disabled = !cb.checked; };
    });

    document.getElementById("sc-save").onclick = async () => {
      const defaults = {};
      schema.defaults.forEach((f) => {
        const id = `sc-defaults-${f.key}`;
        if (document.getElementById(`${id}-toggle`).checked) defaults[f.key] = val(id) || null;
      });
      const toolchain = {};
      schema.toolchain.forEach((f) => {
        const id = `sc-toolchain-${f.key}`;
        if (document.getElementById(`${id}-toggle`).checked) {
          toolchain[f.key] = f.type === "list" ? val(id).split(",").map((s) => s.trim()).filter(Boolean) : (val(id) || null);
        }
      });
      try {
        await api("/api/sidecar/" + encodeURIComponent(name), { method: "PUT", body: { defaults, toolchain } });
        toast("Sidecar saved", "ok");
        loadSidecarCard(name);
      } catch (e) {
        toastError(e);
      }
    };

    document.getElementById("sc-delete").onclick = async () => {
      const ok = await confirmDialog(`Delete the specific config for '${name}'? It will go back to using only the global config.`, { danger: true, confirmLabel: "Delete" });
      if (!ok) return;
      try {
        await api("/api/sidecar/" + encodeURIComponent(name), { method: "DELETE" });
        toast("Sidecar deleted", "ok");
        loadSidecarCard(name);
      } catch (e) {
        toastError(e);
      }
    };
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

function goldBadge() {
  return `<span class="pill pill-golden">${iconSpan("star")}golden</span>`;
}

function currentBadge() {
  return '<span class="pill pill-current">● current</span>';
}

/* How THIS snapshot was built — reader/writer are inferred after the
 * fact from the files actually committed (accurate even with an
 * ad-hoc --to writer never written to config), not from "what the
 * config would resolve now". For an explicit pipeline we don't
 * summarize the stages inline (misleading/incomplete for a fan-out or
 * exec stage) — a badge with hover (native title, "hover type") shows
 * the full sequence only when it's really needed. */
function _snapshotBuildInfoHtml(s) {
  if (s.pipeline_explicit) {
    const detail = s.pipeline_description || "explicit pipeline";
    return `<span class="pill pill-dim snapshot-pipeline-badge" title="${escapeHtml(detail)}">${iconSpan("box")}explicit pipeline</span>`;
  }
  const bits = [s.reader, (s.writers && s.writers.length) ? s.writers.join(" + ") : null].filter(Boolean);
  return bits.length ? `<div class="snapshot-build-info mono">${escapeHtml(bits.join(" → "))}</div>` : "";
}

/* Golden summary above the commit form: 4-value status (match/
 * mismatch/stale/missing) — 'stale' is the new case, the source
 * changed after golden so the output comparison is no longer
 * reliable, distinct from a real mismatch. */
async function loadGoldenSummary(name) {
  const el = document.getElementById("golden-summary");
  try {
    const g = await api("/api/golden/" + encodeURIComponent(name));
    el.innerHTML = render`
      <div class="result-line">
        <strong>Golden:</strong>
        ${statusPill(g.status)}
        ${raw(g.golden_snapshot_id ? `<span class="subtitle">snapshot #${g.golden_snapshot_id}</span>` : "")}
        ${raw(g.golden_snapshot_id ? '<button class="ghost" id="btn-golden-clear">Remove</button>' : "")}
      </div>
    `;
    const clearBtn = document.getElementById("btn-golden-clear");
    if (clearBtn) {
      clearBtn.onclick = async () => {
        const ok = await confirmDialog(`Remove the golden reference for '${name}'? Snapshots stay, only the pointer is removed.`, { danger: true, confirmLabel: "Remove" });
        if (!ok) return;
        try {
          await api("/api/golden/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Golden removed", "ok");
          loadGoldenSummary(name);
          loadHistory(name);
        } catch (e) {
          toastError(e);
        }
      };
    }
  } catch (e) {
    el.innerHTML = "";
  }
}

function _snapshotItemHtml(s, name, goldenId, headId) {
  const isGolden = s.id === goldenId;
  const isCurrent = s.id === headId;
  const isIncomplete = s.missing_outputs && s.missing_outputs.length > 0;
  const itemClasses = ["snapshot-item", isGolden ? "is-golden" : "", isCurrent ? "is-current" : "", isIncomplete ? "is-incomplete" : ""]
    .filter(Boolean).join(" ");
  return render`
    <div class="${itemClasses}">
      <div class="snapshot-item-head">
        <span class="snapshot-id mono">#${s.id}</span>
        ${raw((isCurrent ? currentBadge() : "") + (isGolden ? goldBadge() : ""))}
      </div>
      <div class="snapshot-item-meta mono">${s.timestamp}</div>
      <div class="snapshot-item-msg">${s.message}</div>
      ${raw(_snapshotBuildInfoHtml(s))}
      <div class="snapshot-item-outputs mono">${s.outputs.length ? s.outputs.join(", ") : "—"}</div>
      ${raw(isIncomplete
        ? `<div class="snapshot-warning">${iconSpan("warnTri")}incomplete pipeline — missing ${escapeHtml(s.missing_outputs.join(", "))}</div>`
        : "")}
      <div class="table-actions">
        ${raw(isCurrent
          ? `<button disabled title="Already the current snapshot">Restore</button>`
          : `<button data-restore="${s.id}">Restore</button>`)}
        ${raw(isGolden ? "" : `<button class="ghost" data-set-golden="${s.id}">${iconSpan("star")}Golden</button>`)}
        <a class="btn" href="/api/log/${encodeURIComponent(name)}/${s.id}/download" download>${icon("save")}Download</a>
      </div>
    </div>`;
}

function _loadMoreButtonHtml(offset, remaining) {
  return `<button class="ghost" id="btn-load-more-snapshots" data-offset="${offset}">${iconSpan("down")}Load more (${remaining} remaining)</button>`;
}

/* History is loaded in pages (see HISTORY_PAGE_SIZE): with many
 * snapshots a single table would make the page very long and
 * unbalanced compared to the main column (History is docked to the
 * side, see .table-detail-side). A single delegated listener on
 * #history-result handles restore/golden/load-more: adding further
 * pages doesn't require re-attaching handlers to the new nodes. */
async function loadHistory(name) {
  const el = document.getElementById("history-result");
  try {
    const [log, golden] = await Promise.all([
      api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=0`),
      api("/api/golden/" + encodeURIComponent(name)),
    ]);
    if (!log.snapshots.length) {
      el.innerHTML = '<p class="empty-state">No snapshot yet.</p>';
      return;
    }
    const goldenId = golden.golden_snapshot_id;
    const items = log.snapshots.map((s) => _snapshotItemHtml(s, name, goldenId, log.head_snapshot_id));
    el.innerHTML = render`
      <div class="snapshot-list" id="snapshot-list">${items}</div>
      ${raw(log.has_more ? _loadMoreButtonHtml(log.snapshots.length, log.total - log.snapshots.length) : "")}
    `;

    el.onclick = async (event) => {
      const restoreBtn = event.target.closest("[data-restore]");
      if (restoreBtn) {
        const snapshotId = Number(restoreBtn.getAttribute("data-restore"));
        const preview = await api("/api/restore", { body: { table_name: name, snapshot_id: snapshotId } });
        const previewLabel = preview.source || preview.sources.join(", ");
        const ok = await confirmDialog(`Overwrite ${previewLabel} with the state of snapshot #${snapshotId}?`, { danger: true, confirmLabel: "Overwrite" });
        if (!ok) return;
        const r = await api("/api/restore", { body: { table_name: name, snapshot_id: snapshotId, confirm: true } });
        const removedNote = r.removed.length ? ` — removed (weren't part of the snapshot): ${r.removed.join(", ")}` : "";
        toast(`Restored: ${r.written.join(", ")}${removedNote}`, "ok");
        loadSource(name);
        loadPipelineBuilder(name);
        loadHistory(name);
        loadGoldenSummary(name);
        return;
      }

      const goldenBtn = event.target.closest("[data-set-golden]");
      if (goldenBtn) {
        const snapshotId = Number(goldenBtn.getAttribute("data-set-golden"));
        try {
          await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
          toast(`Golden set to snapshot #${snapshotId}`, "ok");
          loadHistory(name);
          loadGoldenSummary(name);
        } catch (e) {
          toastError(e);
        }
        return;
      }

      const loadMoreBtn = event.target.closest("#btn-load-more-snapshots");
      if (loadMoreBtn) {
        const offset = Number(loadMoreBtn.getAttribute("data-offset"));
        loadMoreBtn.disabled = true;
        try {
          const next = await api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`);
          const list = document.getElementById("snapshot-list");
          const nextHtml = next.snapshots.map((s) => _snapshotItemHtml(s, name, goldenId, next.head_snapshot_id)).join("");
          list.insertAdjacentHTML("beforeend", nextHtml);
          const newOffset = offset + next.snapshots.length;
          if (next.has_more) {
            loadMoreBtn.outerHTML = _loadMoreButtonHtml(newOffset, next.total - newOffset);
          } else {
            loadMoreBtn.remove();
          }
        } catch (e) {
          toastError(e);
          loadMoreBtn.disabled = false;
        }
      }
    };
  } catch (e) {
    el.innerHTML = render`<p class="empty-state">${e.message}</p>`;
  }
}

function val(id) { return document.getElementById(id).value.trim(); }
function chk(id) { return document.getElementById(id).checked; }

/* ---------- build all (SSE) ---------- */

function viewBuildAll() {
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Build all", "Builds every table discovered under the project root."))}
    <div class="card">
      <div class="field-row">
        <div class="field"><label>Writer (--to)</label><div class="autocomplete-wrap"><input type="text" id="ba-to" placeholder="bin"></div></div>
        <div class="field"><label>Filter glob</label><input type="text" id="ba-filter" placeholder="sensors/**"></div>
        <div class="field"><label>Jobs (max ${MAX_BUILD_ALL_JOBS})</label><input type="number" id="ba-jobs" value="1" min="1" max="${MAX_BUILD_ALL_JOBS}" style="width:80px"></div>
      </div>
      <div class="toggle-chip-row">
        <label class="toggle-chip"><input type="checkbox" id="ba-force"><span>--force</span></label>
        <label class="toggle-chip"><input type="checkbox" id="ba-golden"><span>--check-golden</span></label>
      </div>
      <div class="build-actions">
        <button class="primary" id="ba-start">${icon("play")}Start build-all</button>
      </div>
      <h2>Log</h2>
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
    const es = new EventSource("/api/build-all/stream?" + params.toString());
    const appendLine = (text) => {
      const line = document.createElement("div");
      line.className = "log-line";
      line.textContent = text;
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
    };
    es.addEventListener("progress", (ev) => {
      const d = JSON.parse(ev.data);
      appendLine(`${d.status === "ok" ? "✓" : "✗"} ${d.source}`);
    });
    es.addEventListener("summary", (ev) => {
      const d = JSON.parse(ev.data);
      appendLine(`— done: ${d.built} built, ${d.cached} from cache, ${d.golden_mismatch} golden mismatch, ${d.errors} errors`);
      es.close();
      btn.disabled = false;
    });
    es.addEventListener("error", (ev) => {
      if (ev.data) {
        const d = JSON.parse(ev.data);
        appendLine(`✗ ${d.message}`);
      }
      es.close();
      btn.disabled = false;
    });
  };
}

/* ---------- plugins ---------- */

const PLUGIN_KIND_META = {
  reader: { title: "Reader", icon: "book" },
  writer: { title: "Writer", icon: "save" },
  doctor_check: { title: "Doctor check", icon: "check" },
};

function _pluginCardHtml(p) {
  const meta = PLUGIN_KIND_META[p.kind];
  const extPills = p.extensions.map((e) => `<span class="pill pill-dim mono">${escapeHtml(e)}</span>`).join("");
  return `
    <a class="plugin-card${p.builtin ? " plugin-card-builtin" : ""}" href="#/plugin/${encodeURIComponent(p.name)}">
      <div class="plugin-card-kind">${iconSpan(meta.icon)}${meta.title}</div>
      <div class="plugin-card-name">${escapeHtml(p.name)}</div>
      <div class="plugin-card-meta">${extPills}<span class="pill pill-dim">v${escapeHtml(p.api_version)}</span></div>
    </a>`;
}

// Used to be 3 boxes (one per kind), each with its own built-ins
// <details> — expanding one grew only that box, so the row never lined
// up (the box's size wasn't fixed, it tracked whatever was open). A
// single flat grid with a filter bar reflows as one predictable unit
// instead, the same way a search result list does, and no box is ever
// bigger or smaller than its neighbor because there ARE no neighbors —
// just one grid.
function _pluginGridHtml(plugins, kindFilter, showBuiltin) {
  const filtered = plugins.filter((p) => (!kindFilter || p.kind === kindFilter) && (showBuiltin || !p.builtin));
  if (filtered.length === 0) {
    return '<p class="empty-state">No plugin matches this filter.</p>';
  }
  return `<div class="plugin-grid">${filtered.map(_pluginCardHtml).join("")}</div>`;
}

function _pluginToolbarHtml(plugins) {
  const counts = { "": plugins.length };
  for (const kind of Object.keys(PLUGIN_KIND_META)) counts[kind] = plugins.filter((p) => p.kind === kind).length;
  const builtinCount = plugins.filter((p) => p.builtin).length;
  const chip = (kind, label) => `
    <button type="button" class="toggle-chip plugin-filter-chip${kind === "" ? " active" : ""}" data-kind-filter="${kind}">
      ${escapeHtml(label)} <span class="pill pill-dim">${counts[kind]}</span>
    </button>`;
  return `
    <div class="plugin-toolbar">
      <div class="toggle-chip-row" id="plugin-kind-filters" style="margin:0">
        ${chip("", "All")}
        ${Object.entries(PLUGIN_KIND_META).map(([kind, meta]) => chip(kind, meta.title)).join("")}
      </div>
      <label class="toggle-chip">
        <input type="checkbox" id="plugin-show-builtin">
        Show payload built-ins <span class="pill pill-dim">${builtinCount}</span>
      </label>
    </div>`;
}

async function viewPlugins() {
  const [r, local] = await Promise.all([api("/api/plugins"), api("/api/local-plugins")]);
  let kindFilter = "";
  
  let showBuiltin = localStorage.getItem("showBuiltinPlugins") === "true";

  const localRows = local.files.map((f) => render`
    <div class="local-plugin-row">
      <span class="mono">${f.filename}</span>
      <span>${raw(f.kinds.length ? f.kinds.map((k) => `<span class="pill pill-dim">${escapeHtml(k)}</span>`).join("") : '<span class="pill pill-fail">not loadable</span>')}${raw(f.stub_methods.length ? `<span class="pill pill-warn" title="${escapeHtml(`Still to implement: ${f.stub_methods.join(", ")}`)}">not implemented</span>` : "")}</span>
      <div class="local-plugin-row-actions">
        <a class="btn" href="#/local-plugin/${encodeURIComponent(f.filename)}">${icon("book")}Open in editor</a>
        <button class="danger icon-only" data-del-local-plugin="${f.filename}" title="Delete local plugin">${icon("trash")}</button>
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Plugins"))}
    ${raw(_pluginToolbarHtml(r.plugins))}
    <div id="plugin-grid-wrap">${raw(_pluginGridHtml(r.plugins, kindFilter, showBuiltin))}</div>
    ${raw(detailsCard(
      "Local plugins (local_plugins/)",
      `<div class="local-plugin-list">${localRows.join("") || '<p class="empty-state">No local plugin in this project.</p>'}</div>`,
      { open: local.files.length > 0 },
    ))}
    <div class="card">
      <h2>New local plugin</h2>
      <div class="field-row">
        <div class="field"><label>Name</label><input type="text" id="pn-name" placeholder="my_format"></div>
        <div class="field"><label>Kind</label>
          <select id="pn-kind"><option value="reader">reader</option><option value="writer">writer</option><option value="doctor-check">doctor-check</option></select>
        </div>
      </div>
      <button class="primary" id="pn-create">${icon("plus")}Create and open in editor</button>
    </div>
  `;

  document.getElementById("pn-create").onclick = async () => {
    try {
      const r2 = await api("/api/plugin/new-local", { body: { name: val("pn-name"), kind: document.getElementById("pn-kind").value } });
      const createdFilename = r2.created.split(/[\\/]/).pop();
      invalidatePluginsCache();
      toast(`Created ${r2.created}`, "ok");
      location.hash = "#/local-plugin/" + encodeURIComponent(createdFilename);
    } catch (e) {
      toastError(e);
    }
  };

  document.querySelectorAll("[data-del-local-plugin]").forEach((btn) => {
    btn.onclick = () => deleteLocalPlugin(btn.dataset.delLocalPlugin, viewPlugins);
  });

  const rerenderPluginGrid = () => {
    document.getElementById("plugin-grid-wrap").innerHTML = _pluginGridHtml(r.plugins, kindFilter, showBuiltin);
  };

  document.querySelectorAll("#plugin-kind-filters [data-kind-filter]").forEach((btn) => {
    btn.onclick = () => {
      kindFilter = btn.dataset.kindFilter;
      document.querySelectorAll("#plugin-kind-filters [data-kind-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      rerenderPluginGrid();
    };
  });

  const showBuiltinCheckbox = document.getElementById("plugin-show-builtin");
  if (showBuiltinCheckbox) {
    showBuiltinCheckbox.checked = showBuiltin;
    showBuiltinCheckbox.onchange = (e) => {
      showBuiltin = e.target.checked;
      localStorage.setItem("showBuiltinPlugins", showBuiltin);
      rerenderPluginGrid();
    };
  }
}


async function deleteLocalPlugin(filename, onDeleted) {
  const ok = await confirmDialog(`Permanently delete '${filename}'? This action can't be undone.`, { danger: true, confirmLabel: "Delete" });
  if (!ok) return;
  try {
    await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "DELETE" });
    invalidatePluginsCache();
    toast(`'${filename}' deleted`, "ok");
    onDeleted();
  } catch (e) {
    toastError(e);
  }
}

async function viewPluginDetail(name) {
  const p = await api("/api/plugin/" + encodeURIComponent(name));

  const chips = [
    p.extensions ? metaChip("Extensions", p.extensions.join(", ")) : "",
    p.extension ? metaChip("Output extension", p.extension) : "",
    p.default_writer ? metaChip("Suggested writer", p.default_writer) : "",
    p.compatible_readers ? metaChip("Only compatible with", p.compatible_readers.join(", ")) : "",
  ].filter(Boolean).join("");

  document.getElementById("content").innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugins</a></div>
    ${raw(pageHeader(p.name, `${p.kind} · API v${p.api_version}${p.builtin ? " · payload built-in" : ""}`))}
    <div class="card">
      ${raw(chips ? `<div class="plugin-meta-row">${chips}</div>` : "")}
      <div class="plugin-description">${raw(formatDescription(p.docstring))}</div>
    </div>
    <div class="card">
      <h2>Validate conformance</h2>
      <div class="field"><label>Sample file (reader only)</label><input type="text" id="pv-sample" placeholder="example.raw"></div>
      <button id="pv-run">Validate</button>
      <div id="pv-result"></div>
    </div>
  `;
  document.getElementById("pv-run").onclick = async () => {
    const r = await api("/api/plugin/validate", { body: { name, sample: val("pv-sample") || undefined } });
    const el = document.getElementById("pv-result");
    if (r.conforms) {
      el.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforms to the contract${r.skipped_behavior_check ? " (structure only, no sample provided)" : ""}</span></div>`;
    } else {
      const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
      el.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>doesn't conform</span><ul>${items}</ul></div>`;
    }
  };
}

/* ---------- local plugin editor (CodeMirror 5, vendored) ---------- */

/* Lazy loading: codemirror.js + mode/python (~420KB together) have no
 * reason to weigh down EVERY page, only the editor's — loaded the
 * first time they're needed, then the same resolved Promise is reused
 * (no double <script> if the editor is reopened). */
let _cmLoadPromise = null;
function loadCodeMirror() {
  if (_cmLoadPromise) return _cmLoadPromise;
  _cmLoadPromise = new Promise((resolveFn, rejectFn) => {
    if (!document.getElementById("cm-css")) {
      const cssLink = document.createElement("link");
      cssLink.id = "cm-css";
      cssLink.rel = "stylesheet";
      cssLink.href = "/static/vendor/codemirror/codemirror.css";
      document.head.appendChild(cssLink);
    }
    const coreScript = document.createElement("script");
    coreScript.src = "/static/vendor/codemirror/codemirror.js";
    coreScript.onload = () => {
      const modeScript = document.createElement("script");
      modeScript.src = "/static/vendor/codemirror/mode/python/python.js";
      modeScript.onload = () => resolveFn(window.CodeMirror);
      modeScript.onerror = () => rejectFn(new Error("Couldn't load the editor (Python mode)"));
      document.body.appendChild(modeScript);
    };
    coreScript.onerror = () => rejectFn(new Error("Couldn't load the editor (codemirror.js)"));
    document.body.appendChild(coreScript);
  });
  return _cmLoadPromise;
}

function _renderPluginTestResults(r) {
  if (!r.loadable) {
    return render`<div class="result-line">${statusPill("fail")}<span>${r.error}</span></div>`;
  }
  return r.results.map((res) => {
    if (!res.loadable) {
      return render`<div class="result-line">${statusPill("fail")}<span><strong>${res.name}</strong> (${res.kind}): ${res.error}</span></div>`;
    }
    const items = (res.issues || []).map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
    return render`
      <div class="result-line">
        ${statusPill(res.conforms ? "ok" : "fail")}
        <span><strong>${res.name}</strong> (${res.kind})${res.skipped_behavior_check ? " — structure only, no sample provided" : ""}</span>
        ${raw(items.length ? `<ul>${items.join("")}</ul>` : "")}
      </div>
    `;
  }).join("");
}

async function viewLocalPluginEditor(rawFilename) {
  const filename = decodeURIComponent(rawFilename);
  const content = document.getElementById("content");

  const [fileData] = await Promise.all([api("/api/local-plugins/" + encodeURIComponent(filename)), loadCodeMirror()]);

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugins</a></div>
    ${raw(pageHeader(filename, "Local plugin editor — edit, check syntax, and test conformance directly from here."))}
    <div class="card">
      <div class="field-row" style="align-items:center">
        <div class="field" style="flex:2;min-width:220px"><label>Sample file for the test (reader only, optional)</label><input type="text" id="lpe-sample" placeholder="example.raw"></div>
        <div class="field" style="flex:0 0 auto"><label>Syntax</label><span class="pill pill-dim" id="lpe-syntax-status">checking…</span></div>
      </div>
      <textarea id="lpe-editor"></textarea>
      <div class="toolbar" style="margin-top:12px">
        <button class="primary" id="lpe-save">${icon("save")}Save</button>
        <button id="lpe-test">Test plugin</button>
        <button class="danger" id="lpe-delete" style="margin-left:auto">${icon("trash")}Delete plugin</button>
      </div>
      <div id="lpe-result"></div>
    </div>
  `;

  const textarea = document.getElementById("lpe-editor");
  textarea.value = fileData.content;
  const cm = window.CodeMirror.fromTextArea(textarea, {
    mode: "python",
    theme: "payload",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    viewportMargin: Infinity,
    extraKeys: { Tab: (instance) => instance.replaceSelection("    ") },
  });

  let errorLine = null;
  const setSyntaxStatus = (html) => {
    const el = document.getElementById("lpe-syntax-status");
    if (el) el.outerHTML = html;
  };
  const runSyntaxCheck = debounce(async () => {
    try {
      const r = await api("/api/local-plugins/syntax-check", { body: { content: cm.getValue() } });
      if (errorLine !== null) { cm.removeLineClass(errorLine, "background", "cm-error-line"); errorLine = null; }
      if (r.valid) {
        setSyntaxStatus(`<span class="pill pill-ok" id="lpe-syntax-status">${iconSpan("check")}valid syntax</span>`);
      } else {
        setSyntaxStatus(`<span class="pill pill-fail" id="lpe-syntax-status">${iconSpan("cross")}line ${r.line || "?"}: ${escapeHtml(r.message || "syntax error")}</span>`);
        if (r.line && r.line - 1 < cm.lineCount()) {
          errorLine = r.line - 1;
          cm.addLineClass(errorLine, "background", "cm-error-line");
        }
      }
    } catch (e) {
      // syntax checking is an extra: a failure here shouldn't interrupt editing
    }
  }, 500);

  cm.on("change", runSyntaxCheck);
  runSyntaxCheck();

  document.getElementById("lpe-save").onclick = async () => {
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
      invalidatePluginsCache();
      toast("Saved", "ok");
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("lpe-test").onclick = async () => {
    const resultEl = document.getElementById("lpe-result");
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
      invalidatePluginsCache();
      const sample = val("lpe-sample");
      const r = await api("/api/local-plugins/" + encodeURIComponent(filename) + "/test", { body: { sample: sample || undefined } });
      resultEl.innerHTML = _renderPluginTestResults(r);
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("lpe-delete").onclick = () => deleteLocalPlugin(filename, () => { location.hash = "#/plugins"; });
}

/* ---------- doctor ---------- */

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

/* ---------- config ---------- */

function _cfgFieldId(section, key) { return `cfg-${section}-${key}`; }

// Labels/descriptions hand-written client-side: the schema coming from
// /api/config only carries key and type (see config_schema() in
// core/config.py), not help text — the backend shouldn't need to know
// how the webapp prefers to explain each field.
const CONFIG_FIELD_META = {
  "defaults.reader": {
    label: "Default reader",
    desc: "Used when --from isn't specified and it can't be inferred from the file extension. Empty = auto-resolution from extension/sniff.",
  },
  "defaults.writer": {
    label: "Default writer",
    desc: "Used when --to isn't specified. Empty = no explicit preference, the reader may suggest one.",
  },
  "defaults.output_dir": {
    label: "Output folder",
    desc: "Where built files end up, relative to the project root.",
  },
  "defaults.cache_dir": {
    label: "Cache folder",
    desc: "Where payload keeps build checkpoints — avoids rebuilding when source and config haven't changed.",
  },
  "defaults.byte_order": {
    label: "Byte order",
    desc: "Default endianness for readers/writers that handle multi-byte values.",
  },
  "toolchain.compiler": {
    label: "Compiler",
    desc: "Executable used by the 'c_source' reader to compile .c files before extracting their bytes.",
  },
  "toolchain.compiler_flags": {
    label: "Compiler flags",
    desc: "Extra flags passed to the compiler, comma-separated — e.g. -O2, -Wall.",
  },
  "toolchain.objcopy": {
    label: "objcopy",
    desc: "Executable used to extract the binary data section after compilation.",
  },
  "toolchain.objcopy_target": {
    label: "objcopy target",
    desc: "Only required by the 'obj' writer — objcopy output format, e.g. elf32-littlearm.",
  },
  "toolchain.objcopy_arch": {
    label: "objcopy arch",
    desc: "Only required by the 'obj' writer — target architecture, e.g. arm.",
  },
};

function _cfgFieldMarkup(section, f, currentByKey, originByKey) {
  const key = `${section}.${f.key}`;
  const value = currentByKey[key];
  const id = _cfgFieldId(section, f.key);
  const meta = CONFIG_FIELD_META[key] || { label: f.key, desc: "" };
  const origin = originByKey[key] || "default";
  // "default" = never customized, using the tool's factory value.
  // "customized" = present in table-tool.toml — has NOTHING to do
  // with any unsaved changes in the form (that's
  // "settings-row-dirty-note" below, deliberately kept separate: they
  // are two different pieces of information, "what's saved" versus
  // "what you're writing right now").
  const originPill = origin === "default"
    ? raw('<span class="pill pill-dim">default</span>')
    : raw('<span class="pill pill-current">customized</span>');

  let control;
  if (f.type === "list") {
    control = render`<input type="text" id="${id}" data-cfg-field="${key}" value="${(value || []).join(", ")}" placeholder="comma-separated">`;
  } else if (f.key === "byte_order") {
    control = `<select id="${id}" data-cfg-field="${key}"><option value="little" ${value !== "big" ? "selected" : ""}>little</option><option value="big" ${value === "big" ? "selected" : ""}>big</option></select>`;
  } else {
    const shown = value === undefined || value === null ? "" : value;
    control = render`<input type="text" id="${id}" data-cfg-field="${key}" value="${shown}" placeholder="${f.key === "writer" || f.key === "reader" ? "no preference" : "(default)"}">`;
  }

  return render`
    <div class="settings-row" data-row-key="${key}">
      <div class="settings-row-info">
        <div class="settings-row-label">${meta.label}${originPill}</div>
        ${raw(meta.desc ? render`<div class="settings-row-desc">${meta.desc}</div>` : "")}
      </div>
      <div class="settings-row-control">
        ${raw(control)}
        <span class="settings-row-dirty-note" hidden>● unsaved change</span>
      </div>
    </div>`;
}

function _cfgReadFormValues(schema) {
  const defaults = {};
  for (const f of schema.defaults) defaults[f.key] = val(_cfgFieldId("defaults", f.key)) || null;
  const toolchain = {};
  for (const f of schema.toolchain) {
    const id = _cfgFieldId("toolchain", f.key);
    toolchain[f.key] = f.type === "list"
      ? val(id).split(",").map((s) => s.trim()).filter(Boolean)
      : (val(id) || null);
  }
  return { defaults, toolchain };
}

// Lightweight preview of the TOML that would be written — not a full
// TOML serializer (no need: only defaults/toolchain, values are always
// string/list/null), just enough to show the user what's about to be
// saved before they press "Save".
function _cfgTomlPreview(values) {
  const fmtVal = (v) => {
    if (Array.isArray(v)) return v.length ? `[${v.map((x) => JSON.stringify(x)).join(", ")}]` : null;
    if (v === null || v === undefined || v === "") return null;
    return JSON.stringify(String(v));
  };
  const section = (name, obj) => {
    const lines = Object.entries(obj).map(([k, v]) => [k, fmtVal(v)]).filter(([, v]) => v !== null).map(([k, v]) => `${k} = ${v}`);
    return `[${name}]` + (lines.length ? `\n${lines.join("\n")}` : "\n# (no override, all defaults)");
  };
  return `${section("defaults", values.defaults)}\n\n${section("toolchain", values.toolchain)}`;
}

async function viewConfig() {
  const r = await api("/api/config");
  const schema = r.schema;
  const currentByKey = Object.fromEntries(r.fields.map((f) => [f.key, f.value]));
  const originByKey = Object.fromEntries(r.fields.map((f) => [f.key, f.origin]));

  const defaultsRows = schema.defaults.map((f) => _cfgFieldMarkup("defaults", f, currentByKey, originByKey));
  const toolchainRows = schema.toolchain.map((f) => _cfgFieldMarkup("toolchain", f, currentByKey, originByKey));

  const rows = r.fields.map((f) => render`
    <tr><td class="mono">${f.key}</td><td class="mono">${JSON.stringify(f.value)}</td><td>${statusPill(f.origin === "default" ? "never_saved" : f.origin.startsWith("sidecar") ? "warn" : "ok")} ${f.origin}</td></tr>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Configuration", "Global project configuration (table-tool.toml) — applies to every table that has no sidecar of its own."))}
    <div class="card settings-section">
      <h2 class="settings-section-title">Default paths and formats</h2>
      <p class="settings-section-desc">Used for every table that has no explicit command-line preference.</p>
      ${defaultsRows}
    </div>
    <div class="card settings-section">
      <h2 class="settings-section-title">Build toolchain</h2>
      <p class="settings-section-desc">Used by the 'c_source' reader and the 'obj' writer — ignore them if you don't use those.</p>
      ${toolchainRows}
      <div class="settings-toolbar">
        <span class="settings-toolbar-status" id="cfg-dirty-status">No changes</span>
        <button type="button" id="cfg-reset">${icon("refresh")}Reset</button>
        <button class="primary" id="cfg-save" disabled>${icon("save")}Save</button>
      </div>
    </div>
    <details class="section-collapse">
      <summary>TOML preview</summary>
      <div class="card" style="margin-top:10px"><pre class="settings-preview" id="cfg-preview"></pre></div>
    </details>
    <details class="section-collapse">
      <summary>Detailed resolution (default → global → sidecar)</summary>
      <div class="card" style="margin-top:10px">
        <div class="table-scroll">
          <table><thead><tr><th>Field</th><th>Value</th><th>Origin</th></tr></thead><tbody>${rows}</tbody></table>
        </div>
      </div>
    </details>
  `;

  const originalValues = _cfgReadFormValues(schema);
  const preview = document.getElementById("cfg-preview");
  const dirtyStatus = document.getElementById("cfg-dirty-status");
  const saveBtn = document.getElementById("cfg-save");

  const fieldOriginal = (section, key) => (section === "defaults" ? originalValues.defaults[key] : originalValues.toolchain[key]);

  const refresh = () => {
    const values = _cfgReadFormValues(schema);
    preview.textContent = _cfgTomlPreview(values);

    let changedCount = 0;
    document.querySelectorAll(".settings-row").forEach((row) => {
      const [section, ...rest] = row.dataset.rowKey.split(".");
      const fieldKey = rest.join(".");
      const current = section === "defaults" ? values.defaults[fieldKey] : values.toolchain[fieldKey];
      const changed = JSON.stringify(current) !== JSON.stringify(fieldOriginal(section, fieldKey));
      row.querySelector(".settings-row-dirty-note").hidden = !changed;
      if (changed) changedCount += 1;
    });

    dirtyStatus.textContent = changedCount ? `${changedCount} unsaved changes` : "No changes";
    dirtyStatus.className = "settings-toolbar-status" + (changedCount ? " settings-toolbar-status-dirty" : "");
    saveBtn.disabled = changedCount === 0;
  };

  document.querySelectorAll("[data-cfg-field]").forEach((el) => el.addEventListener("input", refresh));
  refresh();

  document.getElementById("cfg-reset").onclick = () => viewConfig();

  document.getElementById("cfg-save").onclick = async () => {
    try {
      await api("/api/config", { method: "PUT", body: _cfgReadFormValues(schema) });
      toast("Global configuration saved", "ok");
      viewConfig();
    } catch (e) {
      toastError(e);
    }
  };
}

/* ---------- export & clean ---------- */

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

/* ---------- documentation ---------- */

/* Minimal markdown -> HTML converter: only the subset actually used by
 * the guides bundled with the package (h1-h3 headings, paragraphs,
 * bullet/numbered lists, code blocks, pipe tables, bold, inline code,
 * links) — not a full CommonMark parser. Text is always escaped
 * BEFORE applying bold/code/link (regexes on the delimiters, which
 * survive escaping), never after: same security principle as
 * escapeHtml()/render() above. */
function renderMarkdown(md) {
  function inline(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => `<a href="${href}" target="_blank" rel="noopener">${label}</a>`);
    return s;
  }

  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  const n = lines.length;
  let i = 0;

  while (i < n) {
    const line = lines[i];

    if (line.trim() === "") { i++; continue; }

    if (/^(---+|\*\*\*+|___+)\s*$/.test(line.trim())) {
      out.push("<hr>");
      i++;
      continue;
    }

    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const codeLines = [];
      i++;
      while (i < n && !lines[i].startsWith("```")) { codeLines.push(lines[i]); i++; }
      i++;
      out.push(`<pre class="doc-code"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length + 1; // the page title is already <h1>: sections start at <h2>
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (line.trim().startsWith("|") && lines[i + 1] && /^\s*\|?[\s:-]+\|/.test(lines[i + 1])) {
      const splitRow = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const headerCells = splitRow(line);
      i += 2;
      const rows = [];
      while (i < n && lines[i].trim().startsWith("|")) { rows.push(splitRow(lines[i])); i++; }
      const thead = `<tr>${headerCells.map((c) => `<th>${inline(c)}</th>`).join("")}</tr>`;
      const tbody = rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("");
      out.push(`<div class="table-scroll"><table><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < n && /^[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s+/, "")); i++; }
      out.push(`<ul>${items.map((it) => `<li>${inline(it)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < n && /^\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s+/, "")); i++; }
      out.push(`<ol>${items.map((it) => `<li>${inline(it)}</li>`).join("")}</ol>`);
      continue;
    }

    const paraLines = [];
    while (
      i < n && lines[i].trim() !== "" && !/^```/.test(lines[i]) && !/^#{1,3}\s/.test(lines[i])
      && !/^[-*]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i]) && !lines[i].trim().startsWith("|")
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length) {
      out.push(`<p>${paraLines.map(inline).join(" ")}</p>`);
    } else {
      i++; // line not handled by any branch above: skip to avoid getting stuck
    }
  }

  return out.join("\n");
}

async function viewDocsList() {
  const r = await api("/api/docs");
  const items = r.docs.map((d) => `
    <a class="doc-list-item" href="#/docs/${encodeURIComponent(d.slug)}">
      <h3>${iconSpan("book")}${escapeHtml(d.title)}</h3>
      <p>${escapeHtml(d.description)}</p>
    </a>
  `).join("");
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Documentation", "The guides bundled with the package — no network connection required."))}
    <div class="doc-list">${raw(items)}</div>
  `;
}

async function viewDocDetail(slug) {
  const r = await api("/api/docs/" + encodeURIComponent(slug));
  document.getElementById("content").innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/docs">← Documentation</a></div>
    ${raw(pageHeader(r.title))}
    <div class="card"><div class="doc-content">${raw(renderMarkdown(r.content))}</div></div>
  `;
}

/* ---------- smooth <details> expand/collapse ---------- */

/* Native <details> snaps open/closed instantly, which reads as a bug
 * anywhere its content sits inside a layout that reacts to the size
 * change (e.g. the plugin-columns grid: expanding one column's
 * built-ins accordion used to jump that column's whole box to a new
 * height with no transition). Animate the height instead, delegated
 * on #content so it keeps working across route re-renders without
 * re-binding a listener per <details> every time a page redraws. */
function _animateDetailsToggle(details) {
  if (details._detailsAnim) details._detailsAnim.cancel();
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wasOpen = details.open;
  if (reduceMotion) {
    details.open = !wasOpen;
    return;
  }
  const startHeight = details.offsetHeight;
  // Flip twice, synchronously, to measure the other state's height
  // without ever letting the browser paint the intermediate frame.
  details.open = !wasOpen;
  const endHeight = details.offsetHeight;
  details.open = wasOpen;
  if (!wasOpen) details.open = true;
  details.style.overflow = "hidden";
  requestAnimationFrame(() => {
    const anim = details.animate(
      { height: [`${startHeight}px`, `${endHeight}px`] },
      { duration: 160, easing: "ease-out" }
    );
    details._detailsAnim = anim;
    anim.onfinish = anim.oncancel = () => {
      details.open = !wasOpen;
      details.style.height = "";
      details.style.overflow = "";
      details._detailsAnim = null;
    };
  });
}

document.getElementById("content").addEventListener("click", (e) => {
  const summary = e.target.closest("summary");
  const details = summary && summary.parentElement;
  if (!details || details.tagName !== "DETAILS") return;
  e.preventDefault();
  _animateDetailsToggle(details);
});

/* ---------- bootstrap ---------- */

document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

api("/api/health").then((r) => {
  document.getElementById("root-path").textContent = r.root;
  document.getElementById("root-path").title = r.root;
}).catch(() => {});

router();
