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
  expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>',
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="6" y1="12" x2="18" y2="12"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5c0-1 .8-1.5 2-1.5h6v15H6c-1.2 0-2 .5-2 1.5V5.5Z"/><path d="M20 5.5c0-1-.8-1.5-2-1.5h-6v15h6c1.2 0 2 .5 2 1.5V5.5Z"/></svg>',
  layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 9l10 6 10-6-10-6Z"/><path d="M2 15l10 6 10-6"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6-5.9-3.3-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8Z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M4 19h16"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6V3Z"/><path d="M14 3v4h4"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V5"/><polyline points="7 10 12 5 17 10"/><path d="M4 19h16"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/></svg>',
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

/* ---------- modal (single primitive behind every dialog) ---------- */

/* Core modal. Renders the overlay/box, manages focus (trap + restore),
 * Escape-to-close, overlay-click-to-close, and resolves the promise
 * exactly once. opts:
 *   body         trusted HTML for the box body (callers escape first)
 *   title        optional heading (aria-labelledby when set)
 *   actions      [{ label, className, value, autofocus }] — the promise
 *                resolves with action.value; a function value is called
 *                at click time; value === undefined resolves as "cancel"
 *   cancelValue  value for Cancel/overlay/Escape (default null)
 *   onOpen(box)  called after rendering (custom focus setup)
 */
function openDialog(opts) {
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  const cancelValue = opts.cancelValue !== undefined ? opts.cancelValue : null;
  return new Promise((resolveFn) => {
    const actions = opts.actions || [];
    const actionsHtml = actions.map((a, i) =>
      `<button type="button" id="modal-action-${i}" class="${a.className || ""}"${a.autofocus ? " data-autofocus" : ""}>${escapeHtml(a.label)}</button>`
    ).join("");
    box.innerHTML = render`
      ${opts.title ? raw(`<h3 class="modal-title" id="modal-title">${escapeHtml(opts.title)}</h3>`) : ""}
      ${raw(opts.body || "")}
      <div class="modal-actions">${raw(actionsHtml)}</div>
    `;
    if (opts.title) box.setAttribute("aria-labelledby", "modal-title");
    // large: near-fullscreen modal (hex viewer, pickers) — same sizing
    // the text editor uses
    if (opts.large) box.classList.add("modal-large");
    overlay.hidden = false;

    const previouslyFocused = document.activeElement;
    let resolved = false;
    const finish = (result) => {
      if (resolved) return;
      resolved = true;
      overlay.hidden = true;
      box.classList.remove("modal-large");
      box.removeAttribute("aria-labelledby");
      document.removeEventListener("keydown", onKey);
      if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
      resolveFn(result);
    };
    const onKey = (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); finish(cancelValue); return; }
      if (ev.key === "Tab") {
        // focus trap: keep Tab/Shift+Tab cycling inside the box
        const focusables = Array.from(box.querySelectorAll(
          "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]"
        ));
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
        else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    overlay.onclick = (ev) => { if (ev.target === overlay) finish(cancelValue); };
    box.querySelectorAll("[id^='modal-action-']").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = actions[Number(btn.id.slice("modal-action-".length))];
        const value = typeof action.value === "function" ? action.value() : action.value;
        finish(value === undefined ? cancelValue : value);
      });
    });
    const focusTarget = box.querySelector("[data-autofocus]") || box.querySelector("button.primary") || box.querySelector("button");
    if (focusTarget) focusTarget.focus();
    if (opts.onOpen) opts.onOpen(box);
  });
}


function openTextEditorModal(opts) {
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  const norm = (s) => String(s).replace(/\r\n/g, "\n");
  const originalText = norm(opts.initialContent || "");
  const guardId = opts.guardId || "editor";
  let cm = null;

  box.classList.add("modal-large");
  box.innerHTML = render`
    <h3 class="modal-title">${opts.title || "Edit"}</h3>
    ${opts.subtitle ? raw(`<p class="subtitle mb-14">${escapeHtml(opts.subtitle)}</p>`) : ""}
    ${opts.readOnly ? `<div class="result-line">${icon("warnTri")}<span class="subtitle">Read-only preview — saving is disabled (file too large).</span></div>` : ""}
    <div class="modal-editor-wrap">
      <textarea id="modal-editor" class="source-editor mono" spellcheck="false" rows="20">${opts.initialContent || ""}</textarea>
    </div>
    <div class="modal-actions">
      <span class="subtitle mono flex-1" id="modal-editor-hint">${opts.readOnly ? "read-only" : "unsaved changes are discarded on close"}</span>
      <button type="button" class="ghost" id="modal-editor-cancel">Cancel</button>
      <button type="button" class="primary" id="modal-editor-save" ${opts.readOnly ? "disabled" : ""}>${icon("save")}Save</button>
    </div>
  `;
  overlay.hidden = false;

  const textarea = document.getElementById("modal-editor");
  const saveBtn = document.getElementById("modal-editor-save");
  const cancelBtn = document.getElementById("modal-editor-cancel");

  const close = () => {
    overlay.hidden = true;
    box.classList.remove("modal-large");
    box.innerHTML = "";
    removeDirtyGuard(guardId);
    document.removeEventListener("keydown", onKey);
  };

  const onKey = (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); void finish(false); }
  };
  document.addEventListener("keydown", onKey);

  const dirty = () => !!cm && norm(cm.getValue()) !== originalText;
  registerDirtyGuard(guardId, {
    message: `${opts.title || "The file"} has unsaved changes.`,
    isDirty: dirty,
  });

  const finish = async (saved) => {
    if (!saved && dirty()) {
      const ok = await confirmDialog("Discard unsaved changes?", { danger: true, confirmLabel: "Discard" });
      if (!ok) return;
    }
    close();
  };

  cancelBtn.onclick = () => void finish(false);
  saveBtn.onclick = async () => {
    if (!cm) return;
    saveBtn.disabled = true;
    try {
      await opts.onSave(cm.getValue());
      toast(`${opts.title || "File"} saved`, "ok");
      close();
    } catch (e) {
      toastError(e);
      saveBtn.disabled = false;
    }
  };

  // CM must not block the modal (lazy ~420KB load): the textarea shows
  // immediately, CM replaces it when ready
  loadCodeMirror().then((CM) => {
    if (!document.getElementById("modal-editor")) return; // already closed
    cm = CM.fromTextArea(textarea, {
      mode: (opts.title || "").endsWith(".py") ? "python" : null,
      theme: "payload",
      lineNumbers: true,
      indentUnit: 4,
      tabSize: 4,
      viewportMargin: Infinity,
      readOnly: opts.readOnly ? "nocursor" : false,
      extraKeys: { Tab: (instance) => instance.replaceSelection("    ") },
    });
    if (!opts.readOnly) cm.focus();
  });
}

function confirmDialog(message, opts) {
  opts = opts || {};
  return openDialog({
    title: opts.title,
    body: render`<p>${message}</p>`,
    cancelValue: false,
    // on a destructive confirmation the dangerous button must NOT be
    // the one pre-focused: the first Tab lands on Cancel instead.
    actions: [
      { label: "Cancel", value: false, autofocus: !!opts.danger },
      { label: opts.confirmLabel || "Confirm", className: opts.danger ? "danger" : "primary", value: true, autofocus: !opts.danger },
    ],
  });
}

/* Same modal as confirmDialog, with a text field — used by import
 * (table/batch name to assign to the dropped file). Resolves to the
 * entered text, or null if cancelled/empty. */
function promptDialog(message, opts) {
  opts = opts || {};
  return openDialog({
    title: opts.title,
    body: render`
      <p>${message}</p>
      <div class="field"><input type="text" id="modal-prompt-input" value="${opts.value || ""}" placeholder="${opts.placeholder || ""}"></div>
    `,
    cancelValue: null,
    actions: [
      { label: "Cancel", value: null },
      {
        label: opts.confirmLabel || "Confirm",
        className: "primary",
        value: () => { const input = document.getElementById("modal-prompt-input"); return input ? input.value.trim() || null : null; },
      },
    ],
    onOpen: (box) => {
      const input = box.querySelector("#modal-prompt-input");
      input.focus();
      input.select();
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); const primary = box.querySelector("button.primary"); if (primary) primary.click(); }
      });
    },
  });
}

/* Info-only modal (single "OK" button) — used to report the outcome
 * of a bulk import, where a per-file toast doesn't scale to hundreds
 * of files and some may have been skipped (not a hard failure, see
 * import_many_single_tables in core/table_admin.py). */
function infoDialog(bodyHtml) {
  return openDialog({
    body: bodyHtml,
    cancelValue: undefined,
    actions: [{ label: "OK", className: "primary", autofocus: true }],
  });
}

/* ---------- context menu (right-click, desktop-style) ---------- */

/* items: [{ label, icon, danger, action }]. Closes on outside click,
 * Escape, or window blur; ArrowUp/Down navigate, Enter activates.
 * The caller must preventDefault() on the 'contextmenu' event. */
function openContextMenu(items, x, y) {
  const prev = document.querySelector(".context-menu");
  if (prev) prev.remove();
  const menu = document.createElement("div");
  menu.className = "context-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = items.map((it, i) => `
    <button type="button" class="context-item${it.danger ? " danger" : ""}" data-idx="${i}" role="menuitem">
      ${it.icon ? iconSpan(it.icon) : ""}<span>${escapeHtml(it.label)}</span>
    </button>`).join("");
  document.body.appendChild(menu);

  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - rect.width - 4)) + "px";
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - rect.height - 4)) + "px";

  const buttons = Array.from(menu.querySelectorAll(".context-item"));
  const close = () => {
    if (!menu.isConnected) return;
    menu.remove();
    document.removeEventListener("click", close);
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("blur", close);
  };
  const onKey = (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); close(); return; }
    const idx = buttons.indexOf(document.activeElement);
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const next = buttons[(idx + (ev.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length];
      if (next) next.focus();
    } else if (ev.key === "Enter" && idx >= 0) {
      ev.preventDefault();
      items[idx].action();
      close();
    }
  };
  buttons.forEach((btn, i) => {
    btn.addEventListener("click", () => { items[i].action(); close(); });
  });
  document.addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  window.addEventListener("blur", close);
  if (buttons[0]) buttons[0].focus();
}

/* ---------- unsaved-changes guards ---------- */

/* A view registers a guard while it owns editable content (source
 * editor, plugin editor, config form). The router checks them before
 * navigating away (and beforeunload covers close/refresh). Keyed by id:
 * re-registering the same id replaces the previous guard, so repeated
 * in-page reloads (e.g. loadSource after a restore) don't accumulate.
 * router() calls clearDirtyGuards() at the start of every navigation. */
const _dirtyGuards = new Map();
function registerDirtyGuard(id, guard) { _dirtyGuards.set(id, guard); }
function removeDirtyGuard(id) { _dirtyGuards.delete(id); }
function clearDirtyGuards() { _dirtyGuards.clear(); }
function dirtyGuardActive() {
  for (const guard of _dirtyGuards.values()) {
    if (guard.isDirty()) return guard;
  }
  return null;
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
  return render`<div class="card empty-state">${icon("alert")}<div>${message}</div>${raw(hint ? render`<div class="subtitle mt-6">${hint}</div>` : "")}</div>`;
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
    return '<p class="empty-state py-12">This plugin doesn\'t provide a description.</p>';
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
      <h2 class="card-title">${escapeHtml(title)}</h2>
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

/* ---------- CodeMirror (vendored, lazy-loaded) ---------- */

/* Lazy loading: codemirror.js + mode/python (~420KB together) have no
 * reason to weigh down EVERY page, only the editors' — loaded the first
 * time they're needed, then the same resolved Promise is reused. Used
 * by both the local-plugin editor (plugins.js) and the file browser's
 * text editor (files.js). */
let _cmLoadPromise = null;
function loadCodeMirror() {
  if (_cmLoadPromise) return _cmLoadPromise;
  // fast path: already present (e.g. another editor loaded it) — don't
  // re-inject the <script> tags
  if (window.CodeMirror) {
    _cmLoadPromise = Promise.resolve(window.CodeMirror);
    return _cmLoadPromise;
  }
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

export {
  initTheme, toggleTheme,
  escapeHtml, raw, render, ICONS, iconSpan, icon,
  toast, toastError, openDialog, confirmDialog, promptDialog, infoDialog,
  openContextMenu, loadCodeMirror,
  registerDirtyGuard, removeDirtyGuard, clearDirtyGuards, dirtyGuardActive,
  statusPill, baseName, fmtBytes, debounce, attachAutocomplete,
  pageHeader, skeletonLoading, emptyCard, formatDescription, metaChip, openTextEditorModal,
  detailsCard, pinnedCard, fmtShortTimestamp, goldBadge, currentBadge,
  val, chk, _animateDetailsToggle,
};
