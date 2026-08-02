/* Shared rendering/UI primitives: the XSS-safe template helper
 * (render/raw/escapeHtml), inline SVG icons, toast, modals, pills,
 * autocomplete, cards, and the small formatting utilities used by
 * every view. Split out of the former single-file app.js — no
 * behavior change. */
"use strict";

/* ---------- theme ---------- */

function initTheme() {
  const saved = localStorage.getItem("payload-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
}

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

/* Info-only modal (single "OK" button) — used to report the outcome
 * of a bulk import, where a per-file toast doesn't scale to hundreds
 * of files and some may have been skipped (not a hard failure, see
 * import_many_single_tables in core/table_admin.py). */
function infoDialog(bodyHtml) {
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  return new Promise((resolveFn) => {
    box.innerHTML = render`
      ${raw(bodyHtml)}
      <div class="modal-actions">
        <button type="button" class="primary" id="modal-ok">OK</button>
      </div>
    `;
    overlay.hidden = false;
    const cleanup = () => { overlay.hidden = true; resolveFn(); };
    box.querySelector("#modal-ok").onclick = cleanup;
    overlay.onclick = (ev) => { if (ev.target === overlay) cleanup(); };
  });
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

function goldBadge() {
  return `<span class="pill pill-golden">${iconSpan("star")}golden</span>`;
}

function currentBadge() {
  return '<span class="pill pill-current">● current</span>';
}

function val(id) { return document.getElementById(id).value.trim(); }
function chk(id) { return document.getElementById(id).checked; }

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

export {
  initTheme, toggleTheme,
  escapeHtml, raw, render, ICONS, iconSpan, icon,
  toast, toastError, confirmDialog, promptDialog, infoDialog,
  statusPill, baseName, fmtBytes, debounce, attachAutocomplete,
  pageHeader, skeletonLoading, emptyCard, formatDescription, metaChip,
  detailsCard, pinnedCard, fmtShortTimestamp, goldBadge, currentBadge,
  val, chk, _animateDetailsToggle,
};
