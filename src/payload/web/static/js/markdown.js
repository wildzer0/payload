/* Minimal markdown -> HTML converter: only the subset actually used
 * by the guides bundled with the package (h1-h3 headings, paragraphs,
 * bullet/numbered lists, code blocks, pipe tables, bold, inline code,
 * links) — not a full CommonMark parser. Text is always escaped
 * BEFORE applying bold/code/link (regexes on the delimiters, which
 * survive escaping), never after: same security principle as
 * escapeHtml()/render() in ui.js. Split out of the former
 * single-file app.js — no behavior change. */
"use strict";

import { escapeHtml } from "./ui.js";

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

export { renderMarkdown };
