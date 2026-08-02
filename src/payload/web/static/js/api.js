/* Network layer: a single fetch() wrapper with uniform error handling
 * (the same JSON shape for every error, see web/errors.py), plus the
 * small module-level caches shared across views (plugins list, table
 * source paths). Split out of the former single-file app.js — no
 * behavior change. */
"use strict";

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

/* ---------- shared caches (formerly module-level state in app.js) ---------- */

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

export { api, apiUpload, ApiError, getPlugins, invalidatePluginsCache, ensureTableSources, findSourcePath };
