/* payload web UI — vanilla JS, nessuna dipendenza esterna. Router
 * minimale su location.hash, un helper fetch() con gestione errori
 * uniforme (lo stesso shape JSON per ogni errore, vedi web/errors.py),
 * ed EventSource per le due view live (build-all, watch). */
"use strict";

const COMMIT_MESSAGE_MAX_LENGTH = 1024;
const MAX_BUILD_ALL_JOBS = 32;
const HISTORY_PAGE_SIZE = 4;

/* ---------- tema ---------- */

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

/* ---------- utility di rendering ---------- */

function escapeHtml(value) {
  const s = String(value);
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function raw(value) {
  // marcatore esplicito: contenuto già HTML sicuro, non va escapato
  return { __raw: String(value) };
}

function render(strings, ...values) {
  // Un array interpolato è sempre un elenco di frammenti HTML già
  // pronti (da .map(x => render`...`), o letterali HTML scritti a
  // mano) — non testo utente da scappare, per questo NON passa da
  // escapeHtml: va sempre wrappato in raw() esplicitamente a monte se
  // un domani servisse un array di stringhe utente grezze.
  return strings.reduce((out, s, i) => {
    const v = values[i];
    if (v === undefined) return out + s;
    if (Array.isArray(v)) return out + s + v.map((x) => (x && x.__raw !== undefined ? x.__raw : x)).join("");
    if (v && v.__raw !== undefined) return out + s + v.__raw;
    return out + s + escapeHtml(v);
  }, "");
}

/* ---------- icone (SVG inline, nessun icon-font/CDN) ---------- */

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
};

function iconSpan(name) {
  // stringa HTML semplice (non raw()): per template letterali NON
  // taggati con render (es. il pipeline builder, costruito con
  // stringhe pure per la sua natura molto dinamica/con re-render).
  return `<span class="icon">${ICONS[name] || ""}</span>`;
}

function icon(name) {
  return raw(iconSpan(name));
}

/* ---------- toast (sostituisce il vecchio banner unico) ---------- */

function toast(message, kind, hint) {
  const stack = document.getElementById("toast-stack");
  const el = document.createElement("div");
  el.className = "toast toast-" + (kind || "error");
  el.innerHTML = render`
    <div class="toast-body">
      <div>${message}</div>
      ${raw(hint ? render`<div class="toast-hint">${hint}</div>` : "")}
    </div>
    <button class="toast-close" type="button" aria-label="Chiudi">${icon("close")}</button>
  `;
  el.querySelector(".toast-close").onclick = () => el.remove();
  stack.appendChild(el);
  // tutti i toast spariscono da soli — quelli di errore restano più a
  // lungo (più testo da leggere), il pulsante di chiusura resta comunque
  // disponibile per chiuderli prima.
  const AUTO_DISMISS_MS = { ok: 4000, warn: 6000, error: 8000 };
  setTimeout(() => el.remove(), AUTO_DISMISS_MS[kind] || AUTO_DISMISS_MS.error);
}

function toastError(e) {
  toast(e.message || String(e), "error", e.hint);
}

/* ---------- modale di conferma (sostituisce confirm() nativo) ---------- */

function confirmDialog(message, opts) {
  opts = opts || {};
  const overlay = document.getElementById("modal-overlay");
  const box = document.getElementById("modal-box");
  return new Promise((resolveFn) => {
    box.innerHTML = render`
      <p>${message}</p>
      <div class="modal-actions">
        <button type="button" id="modal-cancel">Annulla</button>
        <button type="button" class="${opts.danger ? "danger" : "primary"}" id="modal-confirm">${opts.confirmLabel || "Conferma"}</button>
      </div>
    `;
    overlay.hidden = false;
    const cleanup = (result) => { overlay.hidden = true; resolveFn(result); };
    box.querySelector("#modal-cancel").onclick = () => cleanup(false);
    box.querySelector("#modal-confirm").onclick = () => cleanup(true);
    overlay.onclick = (ev) => { if (ev.target === overlay) cleanup(false); };
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
    throw new ApiError("Impossibile contattare il server", "Il processo 'pld serve' è ancora in esecuzione?");
  }
  if (res.status === 204) return null;
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
    this.data = data || null; // corpo JSON completo dell'errore — usato dal pipeline builder per leggere 'stage_index'
  }
}

function statusPill(status) {
  const map = {
    ok: ["pill-ok", "ok", "check"], match: ["pill-ok", "match", "check"], clean: ["pill-ok", "invariata", "check"],
    warn: ["pill-warn", "warn", "warnTri"], dirty: ["pill-warn", "modificata", "warnTri"], stale: ["pill-warn", "stale", "warnTri"],
    fail: ["pill-fail", "fail", "cross"], mismatch: ["pill-fail", "mismatch", "cross"], error: ["pill-fail", "errore", "cross"],
    missing: ["pill-dim", "mancante", "dash"], never_saved: ["pill-dim", "mai salvata", "dash"],
    noop: ["pill-dim", "-", "dash"],
  };
  const [cls, label, iconName] = map[status] || ["pill-dim", status || "-", "dash"];
  return raw(`<span class="pill ${cls}">${ICONS[iconName] || ""}${escapeHtml(label)}</span>`);
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

/* ---------- autocomplete leggero (sostituisce <datalist>) ---------- */

/* <datalist> nativa non è stilabile (ogni browser/OS disegna il proprio
 * popup, spesso tagliando le opzioni più lunghe) e stonava col resto
 * dell'interfaccia — questo è un dropdown assoluto, filtrato dal
 * valore digitato, con navigazione da tastiera, costruito con lo
 * stesso CSS di tutto il resto: nessuna dipendenza nuova, solo markup
 * e JS in più. 'input' deve stare dentro un elemento con classe
 * 'autocomplete-wrap' (il dropdown si ancora lì). */
function attachAutocomplete(input, getOptions) {
  const wrap = input.closest(".autocomplete-wrap");
  if (!wrap) return; // difesa: senza wrapper non c'è dove ancorare il dropdown
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
  // mousedown, non click: previene il blur dell'input prima che il click sull'opzione arrivi
  list.addEventListener("mousedown", (ev) => {
    const itemEl = ev.target.closest(".autocomplete-item");
    if (!itemEl) return;
    ev.preventDefault();
    select(items[Array.from(list.children).indexOf(itemEl)]);
  });
}

/* ---------- helper di pagina condivisi ---------- */

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

/* Formattatore leggero per le docstring dei plugin: paragrafi separati
 * da riga vuota, righe che iniziano per "- "/"* " diventano un elenco
 * puntato, righe rientrate (esempi di sintassi, tipo quello di
 * raw_text/csv) diventano un <pre> che preserva gli a capo — non è
 * markdown completo (quello serve per le guide vere, vedi
 * renderMarkdown), solo il minimo per non perdere la struttura che il
 * backend già preserva via inspect.getdoc().
 *
 * Nota: la classificazione è per RIGA, non per blocco separato da riga
 * vuota — una riga tipo "Esempio:" spesso precede l'esempio vero senza
 * una riga vuota in mezzo (vedi RawTextReader/CsvReader), quindi un
 * blocco "tutto o niente" tratterebbe l'intero paragrafo come prosa e
 * perderebbe comunque gli a capo dell'esempio. */
function formatDescription(text) {
  if (!text || !text.trim()) {
    return '<p class="empty-state" style="padding:12px 0">Questo plugin non fornisce una descrizione.</p>';
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

/* Card a scomparsa (<details>): usata per sezioni secondarie o di
 * configurazione — chiusa di default passando open:false, così
 * l'utente decide cosa vedere invece di trovarsi la pagina piena. */
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

/* Card sempre aperta, non collassabile — usata per History sulla
 * pagina tabella: deve restare ben visibile, non nascosta dietro un
 * click come le altre sezioni secondarie. */
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
  [/^\/watch$/, viewWatch],
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
      void content.offsetWidth; // riavvia l'animazione CSS anche su route ripetute
      content.classList.add("route-fade");
      return;
    }
  }
  document.getElementById("content").innerHTML = '<p class="empty-state">Pagina non trovata.</p>';
}

window.addEventListener("hashchange", router);

/* ---------- dashboard ---------- */

/* <select> reader/writer di default per una riga della dashboard:
 * l'opzione 'auto' mostra tra parentesi cosa verrebbe risolto davvero
 * oggi (resolvedValue), le altre opzioni sono l'override esplicito —
 * un solo controllo copre sia "cosa succede" sia "cosa ho scelto".
 * Disabilitato (con badge separato in tabella) se la tabella ha una
 * pipeline esplicita: in quel caso reader/writer di default non si
 * applicano affatto, vengono ignorati dalla risoluzione. */
/* Formato compatto per la colonna 'Ultima modifica' della dashboard:
 * la stringa ISO completa ("2026-08-01T00:21:36") è troppo lunga per
 * una colonna stretta e andava a capo rompendo l'altezza della riga —
 * qui solo giorno/mese/anno-a-2-cifre + ora:minuti, il timestamp
 * completo resta disponibile al passaggio del mouse via title. */
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

/* Evidenzia lo snapshot "attuale" (accent, stesso trattamento della
 * pagina tabella) e, se la punta della cronologia è più avanti (la
 * tabella è ferma a un restore precedente), lo segnala con un chip
 * secondario — altrimenti sparirebbe l'informazione che esistono
 * snapshot più recenti mai "riattivati". */
function _snapshotChipHtml(t) {
  if (!t.last_snapshot) return '<span class="pill pill-dim">mai salvata</span>';
  const current = `<span class="pill pill-current">#${t.last_snapshot.id}</span>`;
  const behindTip = t.tip_snapshot_id && t.tip_snapshot_id !== t.last_snapshot.id;
  const tipNote = behindTip
    ? `<span class="pill pill-dim" title="La cronologia arriva fino allo snapshot #${t.tip_snapshot_id}">punta #${t.tip_snapshot_id}</span>`
    : "";
  return current + tipNote;
}

/* Card per tabella invece di una riga di una grande tabella HTML: con
 * 9 colonne di informazioni diverse (stato, golden, pipeline, sidecar,
 * reader, writer, dimensioni, data, snapshot) una tabella rigida
 * costringeva ogni cella in una larghezza fissa e il contenuto andava
 * a capo rompendo l'allineamento — qui ogni informazione è un chip che
 * si dispone (e va a capo) in autonomia via flexbox, senza mai rompere
 * l'allineamento delle righe sopra/sotto perché non esistono colonne
 * condivise tra le card. */
async function viewDashboard() {
  const [report, status, plugins] = await Promise.all([api("/api/report"), api("/api/status"), getPlugins()]);
  const stateByName = Object.fromEntries(status.tables.map((t) => [t.name, t.state]));
  const readerNames = plugins.plugins.filter((x) => x.kind === "reader").map((x) => x.name);
  const writerNames = plugins.plugins.filter((x) => x.kind === "writer").map((x) => x.name);

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
            ${raw(t.pipeline_explicit ? '<span class="pill pill-warn" title="Pipeline esplicita configurata: sovrascrive reader/writer di default">pipeline</span>' : "")}
            ${raw(t.has_sidecar ? '<span class="pill pill-dim" title="Sidecar (<nome>.config.toml) attivo per questa tabella">override</span>' : "")}
          </div>
          <button class="icon-only" data-quick-build="${t.name}" title="Build rapida (usa reader/writer di default, nessun altro parametro)">${icon("play")}</button>
        </div>
      </div>
      <div class="table-summary-meta">
        <span class="meta-chip meta-chip-control"><strong>Reader</strong>${raw(_defaultSelectHtml("reader", t.name, readerNames, t.reader_override, t.resolved_reader, t.pipeline_explicit))}</span>
        <span class="meta-chip meta-chip-control"><strong>Writer</strong>${raw(_defaultSelectHtml("writer", t.name, writerNames, t.writer_override, t.resolved_writer, t.pipeline_explicit))}</span>
        <span class="meta-chip"><strong>Dimensioni</strong><span class="mono">${fmtBytes(t.source_size)} → ${fmtBytes(t.output_size)}</span></span>
        <span class="meta-chip" title="${t.source_mtime}"><strong>Modificato</strong><span class="mono">${fmtShortTimestamp(t.source_mtime)}</span></span>
        <span class="meta-chip"><strong>Snapshot</strong>${raw(_snapshotChipHtml(t))}</span>
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Dashboard", `${total} tabelle scoperte in questo progetto.`))}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Tabelle totali</div><div class="stat-value">${total}</div></div>
      <div class="stat-card"><div class="stat-label">Sincronizzate</div><div class="stat-value">${synced}</div></div>
      <div class="stat-card ${mismatches ? "stat-fail" : ""}"><div class="stat-label">Golden mismatch/stale</div><div class="stat-value">${mismatches}</div></div>
      <div class="stat-card ${dirty ? "stat-warn" : ""}"><div class="stat-label">Da salvare</div><div class="stat-value">${dirty}</div></div>
    </div>
    <div class="table-summary-list">${cards.length ? cards : ['<p class="empty-state card">Nessuna tabella trovata.</p>']}</div>
  `;

  document.querySelectorAll("[data-quick-build]").forEach((btn) => {
    btn.onclick = async () => {
      const table = btn.dataset.quickBuild;
      btn.disabled = true;
      try {
        const r = await api("/api/build", { body: { source: pathByName[table] } });
        toast(`Build di '${table}' completata: ${r.outputs.join(", ")} (${r.was_built ? "ricostruita" : "da cache"})`, "ok");
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
          // il reader scelto esiste, ma legge DAVVERO il file di questa
          // tabella? stessa verifica di 'Valida conformità' sui plugin,
          // qui puntata sul reader appena impostato come default.
          try {
            const v = await api("/api/source/" + encodeURIComponent(table) + "/validate", { method: "POST" });
            if (v.conforms) {
              toast(`Reader di default per '${table}' aggiornato: legge correttamente il file`, "ok");
            } else {
              toast(`Reader impostato, ma '${v.reader}' non riesce a leggere il sorgente di '${table}'`, "warn", v.issues.map((i) => i.detail).join("; "));
            }
          } catch (ve) {
            toast(`Reader di default per '${table}' aggiornato (verifica non riuscita: ${ve.message})`, "warn");
          }
        } else {
          toast(`${kind === "reader" ? "Reader" : "Writer"} di default per '${table}' aggiornato`, "ok");
        }
        viewDashboard();
      } catch (e) {
        toastError(e);
        sel.disabled = false;
      }
    };
  });
}

/* ---------- dettaglio tabella ---------- */

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
      <label>Messaggio di commit</label>
      <textarea id="commit-message" class="commit-message-input mono" rows="3" maxlength="${COMMIT_MESSAGE_MAX_LENGTH}" placeholder="Descrivi cosa è cambiato…"></textarea>
      <div class="field-hint"><span id="commit-message-count">0</span>/${COMMIT_MESSAGE_MAX_LENGTH}</div>
    </div>
    <div class="toggle-chip-row">
      <label class="toggle-chip"><input type="checkbox" id="commit-golden"><span>${iconSpan("star")}Imposta anche come golden</span></label>
    </div>
    <div class="build-actions">
      <button id="btn-commit">${iconSpan("save")}Commit modifiche</button>
    </div>
    <div id="history-result"></div>
  `;

  content.innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/">← Dashboard</a></div>
    ${raw(pageHeader(name))}
    <div class="table-detail-layout">
      <div class="table-detail-main">
        ${raw(detailsCard("Build", buildBody, { open: true }))}
        ${raw(detailsCard("Contenuto sorgente", '<div id="view-result"><p class="empty-state">—</p></div>', { open: false }))}
        ${raw(detailsCard("Pipeline", '<div id="pipeline-result"></div>', { open: true }))}
        ${raw(detailsCard("Configurazione specifica di questa tabella (sidecar)", '<div id="sidecar-result"></div>', { open: false }))}
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

  // I placeholder di --from/--to riflettono il reader/writer di default
  // già impostato per questa tabella (dashboard o sidecar) — lasciare il
  // campo vuoto userà davvero quel default, scriverci qualcosa lo
  // sovrascrive solo per QUESTA build, senza toccare il default salvato.
  api("/api/report").then((report) => {
    const row = report.tables.find((t) => t.name === name);
    if (!row || row.pipeline_explicit) return;
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
      toast(`Build ok: ${r.outputs.join(", ")} (${r.was_built ? "ricostruito" : "da cache"})`, "ok");
      loadPipelineBuilder(name);
    } catch (e) {
      toastError(e);
    }
  };

  const commitMessageEl = document.getElementById("commit-message");
  const commitMessageCountEl = document.getElementById("commit-message-count");
  commitMessageEl.addEventListener("input", () => { commitMessageCountEl.textContent = commitMessageEl.value.length; });

  document.getElementById("btn-commit").onclick = async () => {
    const message = val("commit-message") || `da web UI: ${name}`;
    const setAsGolden = chk("commit-golden");
    try {
      const r = await api("/api/commit", { body: { message, only: [name] } });
      if (!r.committed.length) {
        toast("Niente da salvare", "ok");
        return;
      }
      const snapshotId = r.committed[0].snapshot_id;
      const missing = r.committed[0].missing_outputs || [];
      if (setAsGolden) {
        await api("/api/golden/" + encodeURIComponent(name), { method: "PUT", body: { snapshot_id: snapshotId } });
      }
      if (missing.length) {
        toast(
          `Snapshot #${snapshotId} salvato, ma la pipeline è incompleta${setAsGolden ? " (★ golden)" : ""}`,
          "warn",
          `Manca: ${missing.join(", ")} — un writer del gruppo non ha prodotto output`
        );
      } else {
        toast(`Snapshot #${snapshotId} salvato${setAsGolden ? " (★ golden)" : ""}`, "ok");
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

  await Promise.all([
    ensureTableSources(), loadSource(name), loadPipelineBuilder(name),
    loadSidecarCard(name), loadHistory(name), loadGoldenSummary(name),
  ]);
}

let _tableSources = null;
function findSourcePath(name) {
  return (_tableSources && _tableSources[name]) || name;
}
/* Popola _tableSources (nome tabella -> path assoluto) UNA VOLTA per
 * pagina, PRIMA che qualunque handler possa averne bisogno (Build in
 * primis) — deve essere chiamata da viewTable() stessa, non solo dai
 * rami che per caso passano da /api/status (es. il fallback esadecimale
 * dei sorgenti non testuali): altrimenti il Build su una tabella
 * editabile (il caso comune) userebbe il solo nome tabella invece del
 * path, e il backend risponderebbe 'file non trovato'. */
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
  return `<div class="log">${hexLines.join("") || '<span class="log-empty">vuoto</span>'}</div>`;
}

/* Editor del contenuto sorgente direttamente in pagina: solo per
 * formati testuali (CSV, testo grezzo, C, ...) — un file che non
 * decodifica come UTF-8 (blob binario) resta di sola lettura, mostrato
 * come esadecimale come prima, con un avviso invece che un editor che
 * lo corromperebbe al primo salvataggio. */
async function loadSource(name) {
  const el = document.getElementById("view-result");
  try {
    const info = await api("/api/source/" + encodeURIComponent(name));
    if (info.editable) {
      el.innerHTML = render`
        <textarea id="source-editor" class="source-editor mono" spellcheck="false" rows="14">${info.content}</textarea>
        <div class="source-editor-actions">
          <button class="primary" id="btn-save-source">${icon("save")}Salva sorgente</button>
          <button id="btn-validate-source">${icon("check")}Valida con reader di default</button>
          <span class="subtitle mono">${info.path}</span>
        </div>
        <div id="source-validate-result"></div>
      `;
      const runValidate = async () => {
        const resultEl = document.getElementById("source-validate-result");
        try {
          const r = await api("/api/source/" + encodeURIComponent(name) + "/validate", { method: "POST" });
          if (r.conforms) {
            resultEl.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforme al reader '${r.reader}'</span></div>`;
          } else {
            const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
            resultEl.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>non conforme al reader '${r.reader}'</span><ul>${items}</ul></div>`;
          }
        } catch (e) {
          toastError(e);
        }
      };
      document.getElementById("btn-save-source").onclick = async () => {
        try {
          await api("/api/source/" + encodeURIComponent(name), { method: "PUT", body: { content: document.getElementById("source-editor").value } });
          toast("Sorgente salvata", "ok");
          runValidate();
        } catch (e) {
          toastError(e);
        }
      };
      document.getElementById("btn-validate-source").onclick = runValidate;
    } else {
      const hex = await hexDumpHtml(name);
      el.innerHTML = render`
        <div class="result-line">${statusPill("warn")}<span>${info.reason} — non modificabile da qui, solo visualizzazione esadecimale.</span></div>
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

/* Builder visuale della pipeline: mostra la risoluzione attuale
 * (implicita da --from/--to, o esplicita da sidecar) come lista di
 * stage modificabile — riordino/aggiunta/rimozione lato client, ma
 * NESSUNA regola di alternanza duplicata qui: 'Salva' manda la lista
 * grezza a PUT /api/pipeline/{table}, che valida con la stessa
 * PipelineSpec.from_raw_stages() del core e risponde con
 * 'stage_index' sull'errore — è quello che evidenzia la card incriminata. */
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
            <input type="text" data-field="command" data-idx="${i}" value="${escapeHtml(s.command || "")}" placeholder="comando esterno, es. objcopy {input} {output}" style="flex:1;min-width:220px">
            <select data-field="on_error" data-idx="${i}">
              <option value="fail" ${s.on_error !== "warn" ? "selected" : ""}>on_error: fail</option>
              <option value="warn" ${s.on_error === "warn" ? "selected" : ""}>on_error: warn</option>
            </select>
            <input type="text" data-field="output_extension" data-idx="${i}" value="${escapeHtml(s.output_extension || "")}" placeholder="estensione se finale, es. .signed.bin" style="width:200px">
          `;
        }
        return `
          <div class="stage-card ${hasError ? "stage-error" : ""}">
            <span class="stage-badge ${badgeCls}">${s.type}</span>
            <div class="stage-fields">${fields}</div>
            <div class="stage-actions">
              <button type="button" class="icon-only ghost" data-move="up" data-idx="${i}" ${i === 0 ? "disabled" : ""} aria-label="Sposta su">${iconSpan("up")}</button>
              <button type="button" class="icon-only ghost" data-move="down" data-idx="${i}" ${i === stages.length - 1 ? "disabled" : ""} aria-label="Sposta giù">${iconSpan("down")}</button>
              <button type="button" class="icon-only ghost danger" data-remove="${i}" aria-label="Rimuovi">${iconSpan("trash")}</button>
            </div>
          </div>
          ${hasError ? `<div class="stage-error-msg">${escapeHtml(lastError.message)}</div>` : ""}
        `;
      }).join("");

      el.innerHTML = `
        <div class="stage-list">${cards || '<p class="empty-state">Nessuno stage — aggiungine uno per iniziare.</p>'}</div>
        <div class="add-stage-row">
          <select id="pb-add-type">
            <option value="reader">reader</option>
            <option value="writer">writer</option>
            <option value="exec">exec</option>
          </select>
          <button type="button" id="pb-add"><span class="icon">${ICONS.plus}</span>Aggiungi stage</button>
          <span style="flex:1"></span>
          <button type="button" id="pb-reset">${iconSpan("refresh")}Ripristina implicita</button>
          <button type="button" class="primary" id="pb-save">${iconSpan("save")}Salva pipeline</button>
        </div>
        <div class="pipeline-output-row">
          <span class="pipeline-output-label">Output</span>
          ${p.outputs.length
            ? p.outputs.map((o) => `<span class="pill pill-dim mono" title="${escapeHtml(o)}">${escapeHtml(baseName(o))}</span>`).join("")
            : '<span class="subtitle">—</span>'}
          ${p.explicit ? '<span class="pill pill-warn">esplicita (sidecar)</span>' : '<span class="pill pill-dim">automatica</span>'}
        </div>
      `;

      // Ogni modifica locale invalida l'errore dell'ultimo tentativo di
      // salvataggio: sia perché gli indici degli stage possono essere
      // cambiati (l'evidenziazione finirebbe sullo stage sbagliato),
      // sia perché l'utente potrebbe aver già corretto il problema —
      // il segnale d'errore deve sparire subito, non restare appiccicato
      // finché non si preme di nuovo Salva.
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
          toast("Pipeline salvata", "ok");
          loadPipelineBuilder(name);
        } catch (e) {
          lastError = { stage_index: e.data && typeof e.data.stage_index === "number" ? e.data.stage_index : -1, message: e.message };
          renderStages();
          toastError(e);
        }
      };

      document.getElementById("pb-reset").onclick = async () => {
        const ok = await confirmDialog("Tornare alla risoluzione automatica da --from/--to? La pipeline esplicita salvata nel sidecar verrà rimossa.", { danger: true, confirmLabel: "Ripristina" });
        if (!ok) return;
        try {
          await api("/api/pipeline/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Pipeline ripristinata alla risoluzione automatica", "ok");
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

/* Card sidecar: uno switch per campo (schema condiviso con la config
 * globale, vedi /api/config 'schema') — solo i campi con lo switch
 * attivo finiscono nel PUT, coerente col modello "il sidecar
 * sovrascrive solo le chiavi che dichiara" di core/config.py. */
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
        inputHtml = `<input type="text" id="${id}" ${has ? "" : "disabled"} value="${escapeHtml((value || []).join(", "))}" placeholder="separati da virgola">`;
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
      <p class="subtitle">Solo i campi selezionati sovrascrivono la config globale per questa tabella.</p>
      <h2 style="margin-top:16px">Defaults</h2>
      ${defaultsRows}
      <h2>Toolchain</h2>
      ${toolchainRows}
      <div class="toolbar" style="margin-top:14px">
        <button type="button" class="primary" id="sc-save">${iconSpan("save")}Salva sidecar</button>
        <button type="button" class="danger" id="sc-delete" ${hasSidecar ? "" : "disabled"}>${iconSpan("trash")}Elimina sidecar</button>
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
        toast("Sidecar salvato", "ok");
        loadSidecarCard(name);
      } catch (e) {
        toastError(e);
      }
    };

    document.getElementById("sc-delete").onclick = async () => {
      const ok = await confirmDialog(`Eliminare la config specifica di '${name}'? Tornerà a usare solo la config globale.`, { danger: true, confirmLabel: "Elimina" });
      if (!ok) return;
      try {
        await api("/api/sidecar/" + encodeURIComponent(name), { method: "DELETE" });
        toast("Sidecar eliminato", "ok");
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
  return '<span class="pill pill-current">● attuale</span>';
}

/* Come è stato costruito QUESTO snapshot — reader/writer sono dedotti
 * a posteriori dai file realmente committati (accurati anche con un
 * writer ad-hoc --to mai scritto in config), non da "cosa risolverebbe
 * la config ora". Per una pipeline esplicita non riassumiamo gli stage
 * in riga (fuorviante/incompleto per un fan-out o stage exec) — un
 * badge con hover (title nativo, "tipo hover") mostra la sequenza
 * completa solo quando serve davvero. */
function _snapshotBuildInfoHtml(s) {
  if (s.pipeline_explicit) {
    const detail = s.pipeline_description || "pipeline esplicita";
    return `<span class="pill pill-dim snapshot-pipeline-badge" title="${escapeHtml(detail)}">${iconSpan("box")}pipeline esplicita</span>`;
  }
  const bits = [s.reader, (s.writers && s.writers.length) ? s.writers.join(" + ") : null].filter(Boolean);
  return bits.length ? `<div class="snapshot-build-info mono">${escapeHtml(bits.join(" → "))}</div>` : "";
}

/* Riepilogo golden sopra la form di commit: stato a 4 valori (match/
 * mismatch/stale/missing) — 'stale' è il caso nuovo, il sorgente è
 * cambiato dopo il golden quindi il confronto sull'output non è più
 * affidabile, distinto da un vero mismatch. */
async function loadGoldenSummary(name) {
  const el = document.getElementById("golden-summary");
  try {
    const g = await api("/api/golden/" + encodeURIComponent(name));
    el.innerHTML = render`
      <div class="result-line">
        <strong>Golden:</strong>
        ${statusPill(g.status)}
        ${raw(g.golden_snapshot_id ? `<span class="subtitle">snapshot #${g.golden_snapshot_id}</span>` : "")}
        ${raw(g.golden_snapshot_id ? '<button class="ghost" id="btn-golden-clear">Rimuovi</button>' : "")}
      </div>
    `;
    const clearBtn = document.getElementById("btn-golden-clear");
    if (clearBtn) {
      clearBtn.onclick = async () => {
        const ok = await confirmDialog(`Rimuovere il riferimento golden per '${name}'? Gli snapshot restano, solo il puntatore viene tolto.`, { danger: true, confirmLabel: "Rimuovi" });
        if (!ok) return;
        try {
          await api("/api/golden/" + encodeURIComponent(name), { method: "DELETE" });
          toast("Golden rimosso", "ok");
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
        ? `<div class="snapshot-warning">${iconSpan("warnTri")}pipeline incompleta — manca ${escapeHtml(s.missing_outputs.join(", "))}</div>`
        : "")}
      <div class="table-actions">
        ${raw(isCurrent
          ? `<button disabled title="È già lo snapshot attuale">Restore</button>`
          : `<button data-restore="${s.id}">Restore</button>`)}
        ${raw(isGolden ? "" : `<button class="ghost" data-set-golden="${s.id}">${iconSpan("star")}Golden</button>`)}
        <a class="btn" href="/api/log/${encodeURIComponent(name)}/${s.id}/download" download>${icon("save")}Scarica</a>
      </div>
    </div>`;
}

function _loadMoreButtonHtml(offset, remaining) {
  return `<button class="ghost" id="btn-load-more-snapshots" data-offset="${offset}">${iconSpan("down")}Carica altri (${remaining} rimanenti)</button>`;
}

/* History è caricata a pagine (vedi HISTORY_PAGE_SIZE): con molti
 * snapshot una singola tabella renderebbe la pagina lunghissima e
 * sbilanciata rispetto alla colonna principale (History è agganciata
 * a fianco, vedi .table-detail-side). Un solo listener delegato su
 * #history-result gestisce restore/golden/carica-altri: aggiungere
 * pagine successive non richiede ri-agganciare handler sui nuovi nodi. */
async function loadHistory(name) {
  const el = document.getElementById("history-result");
  try {
    const [log, golden] = await Promise.all([
      api(`/api/log/${encodeURIComponent(name)}?limit=${HISTORY_PAGE_SIZE}&offset=0`),
      api("/api/golden/" + encodeURIComponent(name)),
    ]);
    if (!log.snapshots.length) {
      el.innerHTML = '<p class="empty-state">Nessuno snapshot ancora.</p>';
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
        const ok = await confirmDialog(`Sovrascrivere ${preview.source} con lo stato dello snapshot #${snapshotId}?`, { danger: true, confirmLabel: "Sovrascrivi" });
        if (!ok) return;
        const r = await api("/api/restore", { body: { table_name: name, snapshot_id: snapshotId, confirm: true } });
        const removedNote = r.removed.length ? ` — rimossi (non facevano parte dello snapshot): ${r.removed.join(", ")}` : "";
        toast(`Ripristinati: ${r.written.join(", ")}${removedNote}`, "ok");
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
          toast(`Golden impostato allo snapshot #${snapshotId}`, "ok");
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
    ${raw(pageHeader("Build all", "Compila tutte le tabelle scoperte sotto la root del progetto."))}
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
        <button class="primary" id="ba-start">${icon("play")}Avvia build-all</button>
      </div>
      <h2>Log</h2>
      <div class="log" id="ba-log"><span class="log-empty">In attesa…</span></div>
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
      appendLine(`— completato: ${d.built} costruite, ${d.cached} da cache, ${d.golden_mismatch} golden mismatch, ${d.errors} errori`);
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

/* ---------- watch (SSE) ---------- */

function viewWatch() {
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Watch", "Osserva i sorgenti e ricompila automaticamente ad ogni salvataggio."))}
    <div class="card">
      <div class="toolbar">
        <button class="primary" id="w-start">${icon("play")}Avvia</button>
        <button id="w-stop">Ferma</button>
        <span class="pill pill-dim" id="w-state">fermo</span>
      </div>
      <h2>Log</h2>
      <div class="log" id="w-log"><span class="log-empty">Non attivo</span></div>
    </div>
  `;

  let es = null;
  const log = document.getElementById("w-log");
  const appendLine = (text) => {
    const line = document.createElement("div");
    line.className = "log-line";
    line.textContent = text;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  };

  document.getElementById("w-start").onclick = async () => {
    const r = await api("/api/watch/start", { body: {} });
    document.getElementById("w-state").outerHTML = '<span class="pill pill-ok" id="w-state">attivo</span>';
    if (r.status === "already_running" && es) return;
    log.innerHTML = "";
    es = new EventSource("/api/watch/stream");
    es.addEventListener("change", (ev) => {
      const d = JSON.parse(ev.data);
      appendLine(`${d.status === "ok" ? "✓" : "✗"} ${d.source}${d.status === "ok" ? " → " + d.outputs.join(", ") : " — " + d.message}`);
    });
    es.addEventListener("stopped", () => {
      appendLine("— watch fermato");
      document.getElementById("w-state").outerHTML = '<span class="pill pill-dim" id="w-state">fermo</span>';
      if (es) { es.close(); es = null; }
    });
  };

  document.getElementById("w-stop").onclick = async () => {
    await api("/api/watch/stop", { body: {} });
  };
}

/* ---------- plugin ---------- */

async function viewPlugins() {
  const [r, local] = await Promise.all([api("/api/plugins"), api("/api/local-plugins")]);
  const rows = r.plugins.map((p) => render`
    <tr>
      <td>${p.kind}</td>
      <td><a class="link" href="#/plugin/${p.name}">${p.name}</a></td>
      <td class="mono">${p.extensions.join(", ")}</td>
      <td>v${p.api_version}</td>
    </tr>`);
  const localRows = local.files.map((f) => render`
    <div class="local-plugin-row">
      <span class="mono">${f.filename}</span>
      <span>${raw(f.kinds.length ? f.kinds.map((k) => `<span class="pill pill-dim">${escapeHtml(k)}</span>`).join("") : '<span class="pill pill-fail">non caricabile</span>')}</span>
      <div class="local-plugin-row-actions">
        <a class="btn" href="#/local-plugin/${encodeURIComponent(f.filename)}">${icon("book")}Apri nell'editor</a>
        <button class="danger icon-only" data-del-local-plugin="${f.filename}" title="Elimina plugin locale">${icon("trash")}</button>
      </div>
    </div>
  `);
  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Plugin"))}
    <div class="card">
      <div class="table-scroll">
        <table><thead><tr><th>Tipo</th><th>Nome</th><th>Estensioni</th><th>API</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>
    ${raw(detailsCard(
      "Plugin locali (local_plugins/)",
      `<div class="local-plugin-list">${localRows.join("") || '<p class="empty-state">Nessun plugin locale in questo progetto.</p>'}</div>`,
      { open: local.files.length > 0 },
    ))}
    <div class="card">
      <h2>Nuovo plugin locale</h2>
      <div class="field-row">
        <div class="field"><label>Nome</label><input type="text" id="pn-name" placeholder="my_format"></div>
        <div class="field"><label>Tipo</label>
          <select id="pn-kind"><option value="reader">reader</option><option value="writer">writer</option><option value="doctor-check">doctor-check</option></select>
        </div>
      </div>
      <button class="primary" id="pn-create">${icon("plus")}Crea e apri nell'editor</button>
    </div>
  `;
  document.getElementById("pn-create").onclick = async () => {
    try {
      const r2 = await api("/api/plugin/new-local", { body: { name: val("pn-name"), kind: document.getElementById("pn-kind").value } });
      const createdFilename = r2.created.split(/[\\/]/).pop();
      toast(`Creato ${r2.created}`, "ok");
      location.hash = "#/local-plugin/" + encodeURIComponent(createdFilename);
    } catch (e) {
      toastError(e);
    }
  };

  document.querySelectorAll("[data-del-local-plugin]").forEach((btn) => {
    btn.onclick = () => deleteLocalPlugin(btn.dataset.delLocalPlugin, viewPlugins);
  });
}

async function deleteLocalPlugin(filename, onDeleted) {
  const ok = await confirmDialog(`Eliminare definitivamente '${filename}'? L'azione non è reversibile.`, { danger: true, confirmLabel: "Elimina" });
  if (!ok) return;
  try {
    await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "DELETE" });
    toast(`'${filename}' eliminato`, "ok");
    onDeleted();
  } catch (e) {
    toastError(e);
  }
}

async function viewPluginDetail(name) {
  const p = await api("/api/plugin/" + encodeURIComponent(name));

  const chips = [
    p.extensions ? metaChip("Estensioni", p.extensions.join(", ")) : "",
    p.extension ? metaChip("Estensione output", p.extension) : "",
    p.default_writer ? metaChip("Writer suggerito", p.default_writer) : "",
    p.compatible_readers ? metaChip("Compatibile solo con", p.compatible_readers.join(", ")) : "",
  ].filter(Boolean).join("");

  document.getElementById("content").innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugin</a></div>
    ${raw(pageHeader(p.name, `${p.kind} · API v${p.api_version}`))}
    <div class="card">
      ${raw(chips ? `<div class="plugin-meta-row">${chips}</div>` : "")}
      <div class="plugin-description">${raw(formatDescription(p.docstring))}</div>
    </div>
    <div class="card">
      <h2>Valida conformità</h2>
      <div class="field"><label>Sample file (solo reader)</label><input type="text" id="pv-sample" placeholder="esempio.raw"></div>
      <button id="pv-run">Valida</button>
      <div id="pv-result"></div>
    </div>
  `;
  document.getElementById("pv-run").onclick = async () => {
    const r = await api("/api/plugin/validate", { body: { name, sample: val("pv-sample") || undefined } });
    const el = document.getElementById("pv-result");
    if (r.conforms) {
      el.innerHTML = render`<div class="result-line">${statusPill("ok")}<span>conforme al contratto${r.skipped_behavior_check ? " (solo struttura, nessun sample fornito)" : ""}</span></div>`;
    } else {
      const items = r.issues.map((i) => render`<li><strong>${i.check}</strong>: ${i.detail}</li>`);
      el.innerHTML = render`<div class="result-line">${statusPill("fail")}<span>non conforme</span><ul>${items}</ul></div>`;
    }
  };
}

/* ---------- editor plugin locali (CodeMirror 5, vendorizzato) ---------- */

/* Caricamento pigro: codemirror.js + mode/python (~420KB insieme) non
 * hanno motivo di appesantire OGNI pagina, solo quella dell'editor —
 * caricati la prima volta che serve, poi la stessa Promise risolta
 * viene riusata (niente doppio <script> se si riapre l'editor). */
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
      modeScript.onerror = () => rejectFn(new Error("Impossibile caricare l'editor (mode Python)"));
      document.body.appendChild(modeScript);
    };
    coreScript.onerror = () => rejectFn(new Error("Impossibile caricare l'editor (codemirror.js)"));
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
        <span><strong>${res.name}</strong> (${res.kind})${res.skipped_behavior_check ? " — solo struttura, nessun sample fornito" : ""}</span>
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
    <div class="breadcrumb"><a class="link" href="#/plugins">← Plugin</a></div>
    ${raw(pageHeader(filename, "Editor plugin locale — modifica, verifica la sintassi e testa la conformità direttamente da qui."))}
    <div class="card">
      <div class="field-row" style="align-items:center">
        <div class="field" style="flex:2;min-width:220px"><label>Sample file per il test (solo reader, opzionale)</label><input type="text" id="lpe-sample" placeholder="esempio.raw"></div>
        <div class="field" style="flex:0 0 auto"><label>Sintassi</label><span class="pill pill-dim" id="lpe-syntax-status">in verifica…</span></div>
      </div>
      <textarea id="lpe-editor"></textarea>
      <div class="toolbar" style="margin-top:12px">
        <button class="primary" id="lpe-save">${icon("save")}Salva</button>
        <button id="lpe-test">Testa plugin</button>
        <button class="danger" id="lpe-delete" style="margin-left:auto">${icon("trash")}Elimina plugin</button>
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
        setSyntaxStatus(`<span class="pill pill-ok" id="lpe-syntax-status">${iconSpan("check")}sintassi valida</span>`);
      } else {
        setSyntaxStatus(`<span class="pill pill-fail" id="lpe-syntax-status">${iconSpan("cross")}riga ${r.line || "?"}: ${escapeHtml(r.message || "errore di sintassi")}</span>`);
        if (r.line && r.line - 1 < cm.lineCount()) {
          errorLine = r.line - 1;
          cm.addLineClass(errorLine, "background", "cm-error-line");
        }
      }
    } catch (e) {
      // il controllo sintassi è un extra: un fallimento qui non deve interrompere l'editing
    }
  }, 500);

  cm.on("change", runSyntaxCheck);
  runSyntaxCheck();

  document.getElementById("lpe-save").onclick = async () => {
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
      toast("Salvato", "ok");
    } catch (e) {
      toastError(e);
    }
  };

  document.getElementById("lpe-test").onclick = async () => {
    const resultEl = document.getElementById("lpe-result");
    try {
      await api("/api/local-plugins/" + encodeURIComponent(filename), { method: "PUT", body: { content: cm.getValue() } });
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
        <div class="doctor-item-name">${c.name}</div>
        <div class="doctor-item-message">${c.message}</div>
        ${raw(c.hint ? render`<div class="doctor-item-hint">${c.hint}</div>` : "")}
      </div>
    </div>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Doctor", "Verifica pre-volo di toolchain, plugin, config e directory."))}
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Check totali</div><div class="stat-value">${r.checks.length}</div></div>
      <div class="stat-card"><div class="stat-label">OK</div><div class="stat-value">${counts.ok}</div></div>
      <div class="stat-card ${counts.warn ? "stat-warn" : ""}"><div class="stat-label">Warning</div><div class="stat-value">${counts.warn}</div></div>
      <div class="stat-card ${counts.fail ? "stat-fail" : ""}"><div class="stat-label">Falliti</div><div class="stat-value">${counts.fail}</div></div>
    </div>
    <div class="doctor-list">${items.length ? items : ['<p class="empty-state">Nessun check registrato.</p>']}</div>
  `;
}

/* ---------- config ---------- */

function _cfgFieldId(section, key) { return `cfg-${section}-${key}`; }

function _cfgFieldMarkup(section, f, currentByKey) {
  const value = currentByKey[`${section}.${f.key}`];
  const id = _cfgFieldId(section, f.key);
  if (f.type === "list") {
    return render`<div class="field"><label>${f.key}</label><input type="text" id="${id}" value="${(value || []).join(", ")}" placeholder="separati da virgola"></div>`;
  }
  if (f.key === "byte_order") {
    return render`<div class="field"><label>${f.key}</label><select id="${id}"><option value="little" ${value !== "big" ? "selected" : ""}>little</option><option value="big" ${value === "big" ? "selected" : ""}>big</option></select></div>`;
  }
  const shown = value === undefined || value === null ? "" : value;
  return render`<div class="field"><label>${f.key}</label><input type="text" id="${id}" value="${shown}" placeholder="${f.key === "writer" ? "nessuna preferenza" : "(default)"}"></div>`;
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

async function viewConfig() {
  const r = await api("/api/config");
  const schema = r.schema;
  const currentByKey = Object.fromEntries(r.fields.map((f) => [f.key, f.value]));

  const defaultsFields = schema.defaults.map((f) => _cfgFieldMarkup("defaults", f, currentByKey));
  const toolchainFields = schema.toolchain.map((f) => _cfgFieldMarkup("toolchain", f, currentByKey));

  const rows = r.fields.map((f) => render`
    <tr><td class="mono">${f.key}</td><td class="mono">${JSON.stringify(f.value)}</td><td>${statusPill(f.origin === "default" ? "never_saved" : f.origin.startsWith("sidecar") ? "warn" : "ok")} ${f.origin}</td></tr>
  `);

  document.getElementById("content").innerHTML = render`
    ${raw(pageHeader("Configurazione", "Configurazione globale del progetto (table-tool.toml) — vale per ogni tabella che non ha un sidecar proprio."))}
    <div class="card">
      <h2>Defaults</h2>
      <div class="field-row">${defaultsFields}</div>
      <h2>Toolchain</h2>
      <div class="field-row">${toolchainFields}</div>
      <button class="primary" id="cfg-save">${icon("save")}Salva</button>
    </div>
    <details class="section-collapse">
      <summary>Risoluzione dettagliata (default → globale → sidecar)</summary>
      <div class="card" style="margin-top:10px">
        <div class="table-scroll">
          <table><thead><tr><th>Campo</th><th>Valore</th><th>Origine</th></tr></thead><tbody>${rows}</tbody></table>
        </div>
      </div>
    </details>
  `;

  document.getElementById("cfg-save").onclick = async () => {
    try {
      await api("/api/config", { method: "PUT", body: _cfgReadFormValues(schema) });
      toast("Configurazione globale salvata", "ok");
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
      <p class="subtitle">Scarica un archivio .zip con sorgenti e config del progetto.</p>
      <div class="checkbox-row"><input type="checkbox" id="ex-history"><label for="ex-history">Includi .payload_history/</label></div>
      <a class="btn" id="ex-download" href="#">Scarica .zip</a>
    </div>
    <div class="card">
      <h2>Pulisci</h2>
      <div class="field"><label>Target</label>
        <select id="cl-target"><option value="cache">cache</option><option value="build">build</option><option value="golden">golden</option><option value="all">all</option></select>
      </div>
      <button class="danger" id="cl-run">${icon("trash")}Pulisci</button>
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
    if (preview.status === "noop") { el.innerHTML = '<p class="empty-state">Niente da pulire.</p>'; return; }
    const ok = await confirmDialog(`Cancellare: ${preview.directories.join(", ")}?`, { danger: true, confirmLabel: "Cancella" });
    if (!ok) return;
    const r = await api("/api/clean", { body: { target, confirm: true } });
    el.innerHTML = render`<p>${statusPill("ok")} rimosse: ${r.directories.join(", ")}</p>`;
  };
}

/* ---------- documentazione ---------- */

/* Convertitore markdown -> HTML minimale: solo il sottoinsieme usato
 * davvero dalle guide incluse nel pacchetto (titoli h1-h3, paragrafi,
 * elenchi puntati/numerati, blocchi di codice, tabelle pipe, grassetto,
 * codice inline, link) — non un parser CommonMark completo. Il testo
 * viene sempre escapato PRIMA di applicare bold/code/link (regex sui
 * delimitatori, che sopravvivono all'escaping), mai dopo: stesso
 * principio di sicurezza di escapeHtml()/render() sopra.*/
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
      const level = heading[1].length + 1; // il titolo pagina è già <h1>: le sezioni partono da <h2>
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
      i++; // riga non gestita da nessun ramo sopra: salta per non restare bloccati
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
    ${raw(pageHeader("Documentazione", "Le guide incluse nel pacchetto — nessuna connessione di rete richiesta."))}
    <div class="doc-list">${raw(items)}</div>
  `;
}

async function viewDocDetail(slug) {
  const r = await api("/api/docs/" + encodeURIComponent(slug));
  document.getElementById("content").innerHTML = render`
    <div class="breadcrumb"><a class="link" href="#/docs">← Documentazione</a></div>
    ${raw(pageHeader(r.title))}
    <div class="card"><div class="doc-content">${raw(renderMarkdown(r.content))}</div></div>
  `;
}

/* ---------- bootstrap ---------- */

document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

api("/api/health").then((r) => {
  document.getElementById("root-path").textContent = r.root;
  document.getElementById("root-path").title = r.root;
}).catch(() => {});

router();
