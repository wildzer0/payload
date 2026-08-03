/* Command palette (Ctrl/Cmd+K): search tables (by name or tag), files,
 * pages and actions from one overlay — the dashboard's old static
 * search box lives here now. Built with plain DOM + textContent so the
 * results can never leak HTML. */
"use strict";

import { api } from "./api.js";

let _palette = null; // { overlay, input, results, footer }

const PAGES = [
  { label: "Dashboard", action: () => { location.hash = "#/"; } },
  { label: "Files", action: () => { location.hash = "#/files"; } },
  { label: "Plugins", action: () => { location.hash = "#/plugins"; } },
  { label: "Clusters", action: () => { location.hash = "#/clusters"; } },
  { label: "Doctor", action: () => { location.hash = "#/doctor"; } },
  { label: "Configuration", action: () => { location.hash = "#/config"; } },
  { label: "Export & clean", action: () => { location.hash = "#/tools"; } },
  { label: "Documentation", action: () => { location.hash = "#/docs"; } },
  { label: "Activity log", action: () => { location.hash = "#/log"; } },
];

async function _paletteData() {
  const [report, fileList] = await Promise.all([
    api("/api/report").catch(() => ({ tables: [] })),
    api("/api/fs/list").catch(() => ({ files: [] })),
  ]);
  const tables = (report.tables || []).map((t) => ({
    group: "Tables",
    label: t.name,
    hint: [t.tags && t.tags.length ? `tags: ${t.tags.join(", ")}` : null, t.cluster ? `cluster: ${t.cluster}` : null]
      .filter(Boolean).join(" · "),
    action: () => { location.hash = "#/table/" + encodeURIComponent(t.name); },
  }));
  // every file in the project (not just the root), via /api/fs/list
  const files = (fileList.files || []).map((rel) => ({
    group: "Files",
    label: rel.split("/").pop(),
    hint: rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "/",
    action: () => { location.hash = "#/files/" + encodeURIComponent(rel); },
  }));
  return [...tables, ...files];
}

function _buildPalette() {
  const overlay = document.createElement("div");
  overlay.className = "palette-overlay";
  overlay.innerHTML = `
    <div class="palette" role="dialog" aria-label="Command palette">
      <input class="palette-input" placeholder="Search tables, files, pages, actions…">
      <div class="palette-results"></div>
      <div class="palette-footer">↑↓ navigate · Enter open · Esc close</div>
    </div>`;
  overlay.addEventListener("mousedown", (ev) => { if (ev.target === overlay) closePalette(); });
  document.body.appendChild(overlay);
  return {
    overlay,
    input: overlay.querySelector(".palette-input"),
    results: overlay.querySelector(".palette-results"),
    footer: overlay.querySelector(".palette-footer"),
  };
}

function closePalette() {
  if (!_palette) return;
  _palette.overlay.remove();
  _palette = null;
}

/* Renders the given items (already filtered), grouped, with the active
 * index highlighted. items: [{group, label, hint, action}]. */
function _renderResults(items, activeIndex) {
  const el = _palette.results;
  el.textContent = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "palette-empty";
    empty.textContent = "No match.";
    el.appendChild(empty);
    return;
  }
  let lastGroup = null;
  items.forEach((item, i) => {
    if (item.group !== lastGroup) {
      lastGroup = item.group;
      const header = document.createElement("div");
      header.className = "palette-group";
      header.textContent = item.group;
      el.appendChild(header);
    }
    const row = document.createElement("button");
    row.type = "button";
    row.className = "palette-item" + (i === activeIndex ? " active" : "");
    const label = document.createElement("span");
    label.className = "palette-item-label";
    label.textContent = item.label;
    row.appendChild(label);
    if (item.hint) {
      const hint = document.createElement("span");
      hint.className = "palette-item-hint";
      hint.textContent = item.hint;
      row.appendChild(hint);
    }
    row.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      item.action();
      closePalette();
    });
    el.appendChild(row);
  });
  const active = el.querySelector(".palette-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

export async function openPalette() {
  if (_palette) { _palette.input.focus(); return; }
  _palette = _buildPalette();
  const { input, results } = _palette;

  let allItems = [];
  let filtered = [];
  let activeIndex = 0;

  const filterItems = () => {
    const q = input.value.trim().toLowerCase();
    filtered = q
      ? allItems.filter((i) => `${i.label} ${i.hint || ""}`.toLowerCase().includes(q))
      : allItems;
    activeIndex = 0;
    _renderResults(filtered, activeIndex);
  };

  const run = () => {
    if (!filtered.length) return;
    filtered[activeIndex].action();
    closePalette();
  };

  input.addEventListener("input", filterItems);
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); activeIndex = Math.min(activeIndex + 1, filtered.length - 1); _renderResults(filtered, activeIndex); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); activeIndex = Math.max(activeIndex - 1, 0); _renderResults(filtered, activeIndex); }
    else if (ev.key === "Enter") { ev.preventDefault(); run(); }
    else if (ev.key === "Escape") { ev.preventDefault(); closePalette(); }
  });

  _palette.input.value = "";
  _renderResults([{ group: "…", label: "Loading…", hint: "", action: () => {} }], -1);
  input.focus();
  allItems = await _paletteData();
  if (!_palette) return; // closed while loading
  filterItems();
}
