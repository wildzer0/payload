// Minimal browser API surface to run the payload UI modules under jsc.
// Kept in-repo (tests/js_harness) so the harness survives /tmp cleanup.
function makeClassList() {
  const s = new Set();
  return {
    add: (...c) => c.forEach((x) => s.add(x)), remove: (...c) => c.forEach((x) => s.delete(x)),
    toggle: (c, f) => { if (f === undefined) { if (s.has(c)) { s.delete(c); return false; } s.add(c); return true; } f ? s.add(c) : s.delete(c); return f; },
    contains: (c) => s.has(c),
  };
}
function makeElement() {
  return {
    _v: "", innerHTML: "", textContent: "", value: "", checked: false, hidden: false, disabled: false,
    className: "", id: "", tagName: "DIV", dataset: {}, style: {}, children: [], _listeners: {}, _phantom: [],
    classList: makeClassList(),
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); },
    removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter((f) => f !== fn); },
    fire(t, ev) {
      (this._listeners[t] || []).forEach((fn) => fn(ev || {}));
      const prop = this["on" + t];
      if (typeof prop === "function") prop.call(this, ev || {});
    },
    click() { this.fire("click"); },
    appendChild(c) { this.children.push(c); return c; }, removeChild() {}, insertBefore() {}, insertAdjacentHTML(pos, html) { this.innerHTML += html; },
    querySelector(sel) {
      if (this._phantom[0]) return this._phantom[0];
      if (sel === ".toast-close") { this._toastClose = this._toastClose || makeElement(); return this._toastClose; }
      if (sel === "[data-table-settings]") { return document._els["dash-settings-btn"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "sensors", isBatch: "1" } }); }
      return null;
    },
    querySelectorAll(sel) {
      if (sel === "[data-table-settings]") {
        const batchBtn = document._els["dash-settings-btn"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "sensors", isBatch: "1" } });
        const singleBtn = document._els["dash-settings-single"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "example_table", isBatch: "" } });
        return [batchBtn, singleBtn];
      }
      return this._phantom;
    },
    closest() { return null; },
    focus() {}, select() {}, scrollIntoView() {}, remove() {},
    getAttribute() { return null; }, setAttribute() {}, add() {}, toggleAttribute() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    onload: null, onerror: null,
  };
}
globalThis.window = globalThis;
globalThis.document = {
  documentElement: makeElement(), head: makeElement(), body: makeElement(), activeElement: null,
  _els: {}, _listeners: {},
  getElementById(id) { return (this._els[id] ||= makeElement()); },
  querySelector(sel) {
    if (sel === "[data-table-settings]") return (this._els["dash-settings-btn"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "sensors", isBatch: "1" } }));
    return null;
  },
  querySelectorAll(sel) {
    if (sel === "[data-table-settings]") {
      const batchBtn = this._els["dash-settings-btn"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "sensors", isBatch: "1" } });
      const singleBtn = this._els["dash-settings-single"] ||= Object.assign(makeElement(), { dataset: { tableSettings: "example_table", isBatch: "" } });
      return [batchBtn, singleBtn];
    }
    return [];
  },
  createElement() { return makeElement(); },
  addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); },
  removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter((f) => f !== fn); },
};
globalThis.localStorage = (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; })();
globalThis.location = { _hash: "#/", get hash() { return this._hash; }, set hash(v) { this._hash = String(v); } };
globalThis.navigator = { userAgent: "jsc" };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.cancelAnimationFrame = () => {};
globalThis.addEventListener = (t, fn) => { (globalThis._listeners ||= {})[t] ||= []; globalThis._listeners[t].push(fn); };
globalThis.__fireWindow = (t, ev) => { (globalThis._listeners || {})[t] ||= []; globalThis._listeners[t].forEach((fn) => fn(ev || {})); };
globalThis.__fireDocument = (t, ev) => { (document._listeners || {})[t] ||= []; document._listeners[t].forEach((fn) => fn(ev || {})); };

const RESPONSES = {
  "/api/health": { status: "ok", root: "/tmp/project", project_name: "demo", project_description: "", version: "0.7.0" },
  "/api/fs/tree": { entries: [
    { name: "temp.raw", rel: "temp.raw", is_dir: false, table_name: "temp", size: 12, mtime: "2026-08-01T10:00:00" },
    { name: "blob.bin", rel: "blob.bin", is_dir: false, table_name: null, size: 12, mtime: "2026-08-01T10:00:00" },
    { name: "sensors", rel: "sensors", is_dir: true, table_name: null, size: 0, mtime: "2026-08-01T10:00:00" },
  ] },
  "/api/fs/list": { files: ["temp.raw", "blob.bin", "sensors/temp.raw", "sensors/other.raw", "side.config.toml", "table-tool.toml"] },
  "/api/fs/read": { path: "temp.raw", name: "temp.raw", size: 12, is_text: true, content: "payload data\n", truncated: false },
  "/api/fs/read?path=blob.bin&as_hex=1": { path: "blob.bin", name: "blob.bin", size: 12, is_text: false,
    rows: [{ offset: 0, hex: "70 61 79 6C 6F 61 64 20 64 61 74 61", ascii: "payload data" }],
    offset: 0, end_offset: 12, limit: 256, has_more: false, can_view_as_text: true },
  "/api/fs/analyze": { path: "blob.bin", size: 100, analyzed: 100, capped: false, entropy: 4.5, printable_ratio: 0.5, distinct: 12, null_ratio: 0.2, ascii_runs: 1, magic: [], freq: [[0x44, 3], [0x41, 2]] },
  "/api/fs/strings?path=blob.bin": { path: "blob.bin", strings: [{ offset: 6, text: "DATA" }], capped: false },
  "/api/fs/search": { matches: [{ path: "temp.raw", offset: 0, hex: "70 61", ascii: "pa" }] },
  "/api/status": { tables: [
    { name: "example_table", path: "example_table.raw", is_batch: false, source_count: 1, state: "clean", cluster: null, tags: [] },
    { name: "sensors", path: "sensors", is_batch: true, source_count: 2, state: "never_saved", cluster: null, tags: [] },
  ] },
  "/api/report": { tables: [
    { name: "sensors", is_batch: true, source_count: 2, tags: [], cluster: null, notes: "", properties: {},
      source_size: 200, output_size: null, byte_order: "little", golden_status: "missing", golden_snapshot_id: null,
      last_snapshot: null, tip_snapshot_id: null, source_mtime: "2026-08-01T10:00:00", has_sidecar: false,
      pipeline_explicit: false, reader_override: null, writer_override: null, resolved_reader: "raw_text", resolved_writer: "bin" },
    { name: "example_table", is_batch: false, cluster: "sensors", tags: ["prod"], notes: "n", properties: { address: "0x8000" },
      source_count: 1, source_size: 100, output_size: 50, byte_order: "little", golden_status: "match", golden_snapshot_id: 1,
      last_snapshot: { id: 2, timestamp: "2026-08-01T10:00:00" }, tip_snapshot_id: 2, source_mtime: "2026-08-01T10:00:00",
      has_sidecar: false, pipeline_explicit: false, reader_override: null, writer_override: null,
      resolved_reader: "raw_text", resolved_writer: "bin" },
  ], warnings: [] },
  "/api/clusters": { clusters: [{ name: "sensors", member_count: 1, members: ["example_table"], defaults: { writer: "hex" }, plugin: {} }] },
  "/api/plugins": { plugins: [
    { kind: "reader", name: "raw_text", extensions: [".raw"], api_version: "1.0", installed: false },
    { kind: "writer", name: "bin", extensions: [".bin"], api_version: "1.0", installed: false },
    { kind: "writer", name: "hex", extensions: [".hex"], api_version: "1.0", installed: false }] },
  "/api/local-plugins": { files: [{ filename: "my_reader.py", size: 10, kinds: ["reader"], stub_methods: [] }] },
  "/api/batch/candidates": { files: ["temp.raw", "blob.bin", "sensors/temp.raw"] },
  "/api/batch": { batches: [{ name: "sensors", sources: ["temp.raw", "blob.bin"], reader: "raw_text", writer: "bin", byte_order: "little" }] },
  "/api/doctor": { checks: [
    { name: "toolchain", status: "ok", message: "cc found: clang 16", hint: null },
    { name: "config", status: "fail", message: "invalid config", hint: "fix table-tool.toml" },
    { name: "plugins", status: "warn", message: "1 plugin not loadable", hint: null },
    { name: "directories", status: "ok", message: "Every directory is writable", hint: null },
  ] },
  "/api/docs": { docs: [{ slug: "plugins", title: "Plugin guide", description: "the contract" }] },
  "/api/docs/plugins": { title: "Plugin guide", content: "see [USAGE.md](USAGE.md) and [pyinstaller](https://pyinstaller.org) and (#anchor)" },
  "/api/source/example_table": { table: "example_table", path: "example_table.raw", editable: true, content: "line1\r\nline2\r\n" },
  "/api/view?source=bin_table&offset=0&limit=256": {
    name: "blob.bin", data_base64: "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", length: 26, offset: 0, limit: 256, has_more: false,
    comments: [{ offset: 0, text: "start of data" }] },
  "/api/pipeline/example_table": { stages: [
    { index: 0, kind: "reader", name: "raw_text" },
    { index: 1, kind: "writer", name: "bin" },
  ], outputs: ["build/example_table.bin"], explicit: false },
  "/api/pipeline/sensors": { stages: [], outputs: [], explicit: false },
  "/api/log": { tables: ["example_table"] },
  "/api/log/example_table": { snapshots: [], has_more: false, total: 0, head_snapshot_id: null },
  "/api/table/example_table/tags": { tags: [] },
  "/api/table/example_table/meta": { cluster: null, tags: [], notes: "", properties: {} },
  "/api/sidecar/example_table": { defaults: {} },
  "/api/table/example_table/analyze": { path: "build/example_table.bin", size: 10, analyzed: 10, capped: false, entropy: 3.2, printable_ratio: 0.5, distinct: 8, null_ratio: 0.1, ascii_runs: 1, magic: [], freq: [[0x41, 5]] },
  "/api/config": { schema: { defaults: [] }, fields: [{ key: "defaults.writer", value: "bin", origin: "default" }] },
};
globalThis.fetch = (path) => {
  const url = String(path);
  const q = url.split("?")[0];
  let data;
  if (q === "/api/fs/read") {
    const p = new URLSearchParams(url.split("?")[1] || "").get("path");
    if (url.includes("as_hex=1") && p === "blob.bin") data = RESPONSES["/api/fs/read?path=blob.bin&as_hex=1"];
    else if (p === "blob.bin") data = { path: "blob.bin", name: "blob.bin", size: 12, is_text: false, can_view_as_text: true, truncated: false };
    else data = RESPONSES["/api/fs/read"];
  } else if (q === "/api/log/activity") {
    const off = parseInt(new URLSearchParams(url.split("?")[1] || "").get("offset") || "0", 10);
    const mk = (n) => ({ ts: 1700000000 + n, kind: "build", detail: "built thing " + n, level: "ok" });
    if (off === 0) data = { events: Array.from({ length: 50 }, (_, i) => mk(100 - i)), total: 60 };
    else data = { events: Array.from({ length: 10 }, (_, i) => mk(50 - i)), total: 60 };
  } else {
    data = RESPONSES[url] !== undefined ? RESPONSES[url] : (RESPONSES[q] || {});
  }
  return Promise.resolve({ status: 200, ok: true, headers: { get: () => "application/json" }, json: () => Promise.resolve(JSON.parse(JSON.stringify(data))) });
};
globalThis.EventSource = class { constructor() {} addEventListener() {} close() {} };
globalThis.FormData = class { constructor() { this._d = new Map(); } append(k, v) { this._d.set(k, v); } get(k) { return this._d.get(k); } };
globalThis.Blob = class { constructor(parts) { this._p = parts; } };
globalThis.URLSearchParams = class {
  constructor(init = "") {
    this._p = new Map();
    if (typeof init === "string" && init) init.replace(/^\?/, "").split("&").filter(Boolean).forEach((kv) => {
      const [k, v] = kv.split("=");
      this._p.set(decodeURIComponent(k), decodeURIComponent(v || ""));
    });
  }
  set(k, v) { this._p.set(k, String(v)); }
  toString() { return [...this._p.entries()].map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&"); }
  get(k) { return this._p.get(k) ?? null; }
};
globalThis.atob = (s) => {
  const b64 = s.replace(/=+$/, "");
  const t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const out = [];
  for (let i = 0; i < b64.length; i += 4) {
    const n0 = t.indexOf(b64[i]); const n1 = t.indexOf(b64[i + 1]);
    const n2 = b64[i + 2] === undefined ? 64 : t.indexOf(b64[i + 2]);
    const n3 = b64[i + 3] === undefined ? 64 : t.indexOf(b64[i + 3]);
    out.push((n0 << 2) | (n1 >> 4));
    if (n2 !== 64) out.push(((n1 & 15) << 4) | (n2 >> 2));
    if (n3 !== 64) out.push(((n2 & 3) << 6) | n3);
  }
  return String.fromCharCode(...out);
};
globalThis.CodeMirror = {
  fromTextArea: (ta) => {
    ta.value = ta.value || "";
    return { getValue: () => ta.value, setValue: (v) => { ta.value = v; }, on: () => {}, removeLineClass: () => {}, addLineClass: () => {}, replaceSelection: (x) => { ta.value += x; }, lineCount: () => 1 };
  },
};
print("[stubs] ready");
