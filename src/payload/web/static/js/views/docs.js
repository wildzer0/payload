/* Documentation views (routes "/docs", "/docs/<slug>"): the bundled
 * guides rendered from markdown (see markdown.js). Split out of the
 * former single-file app.js — no behavior change. */
"use strict";

import { escapeHtml, raw, render, pageHeader, iconSpan } from "../ui.js";
import { api } from "../api.js";
import { renderMarkdown } from "../markdown.js";

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

export { viewDocsList, viewDocDetail };
