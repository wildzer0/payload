# Developer guide — architecture & how to touch the code

How payload is organized, how the pieces fit together, and a
"where to touch to do X" reference for maintainers.

---

## 1. Repo layout

```
src/payload/
  cli.py                  the whole CLI (typer): every command in one file,
                          with small helpers below it
  core/                   backend logic, no I/O to the browser
    ir.py                 TableIR — the in-memory table representation
    plugin_base.py        the Reader/Writer/DoctorCheck plugin contracts
    registry.py           plugin registry + resolution (reader/writer per table)
    pipeline.py           the build pipeline: resolve spec, run stages, cache
    pipeline_spec.py      ReaderStage/WriterStage/ExecStage dataclasses + parse
    discovery.py          project discovery: which files are tables, batch refs
    config.py             table-tool.toml load/mutate (sidecar, batch, plugin)
    batch_tables.py       [[batch_table]] model + effective_config
    clusters.py           [[cluster]] + per-table cluster resolution
    table_meta.py         [[table_meta]] tags / notes / properties
    history.py            snapshots, commit/restore/log/diff, dirty detection
    golden.py             golden status (match/mismatch/stale)
    byteorder.py          little/big repacking of multi-byte fields
    cache.py              the build cache (signature -> outputs)
    table_admin.py        import/clone/rename/delete tables
    file_ops.py           FS operations shared by CLI + web files page
    doctor.py             the doctor check base class + ToolchainCheck
    report.py             the project report (web + `pld report --html`)
    activity.py           the events timeline (events.jsonl)
    errors.py             every exception with its CLI exit code
    local_plugins.py      loose plugins in a project's plugins/ folder
  web/
    app.py                Starlette app factory: routers, static, the SPA
    routes/               one module per API area (see §6)
    static/
      app.js              the SPA router (hash routes) + boot
      index.html          the shell: sidebar, theme, gear
      style.css           the whole UI (single file, ~2900 lines)
      js/
        ui.js             render/raw/icon/iconSpan/toast/dialog/modal helpers
        api.js            the fetch wrapper + getPlugins + table-source cache
        markdown.js       docs rendering + link routing
        palette.js        the command palette (Ctrl/Cmd+K)
        views/            one module per page (dashboard, table, files, …)
examples/plugins/         ready-made readers/writers/doctor checks
tests/                    1347 pytest tests (100% coverage) + js_harness/
tests/js_harness/          the JS regression harness (runs under jsc, in-repo)
tests/deep_testing/        local-only pre-release suites (gitignored, see README)
scripts/check_frontend_js.py  static JS check: imports/exports + template context
```

---

## 2. The three layers

Everything routes through the **core**; the CLI and the web are thin
front-ends over it. A table built from the web and from `pld build`
are the same operation.

```
CLI (cli.py)  ─┐
               ├─►  core/  ──►  the project directory (table-tool.toml,
Web (routes)  ─┘        │        sidecars, build/, .payload_cache/, history/)
                        │
                        └─► plugins (registry + installed/local)
```

Rule of thumb: if a feature touches the project filesystem or the
table model, it lives in `core/`; the CLI and the web routes call it
and map errors to their own output. Never duplicate core logic in a
route or a view.

---

## 3. Core concepts & design

### 3.1 A "table" is a source file

- A single-file table = a source file; its **name is the file's stem**
  (`temperature.csv` → table `temperature`).
- A **batch table** = a `[[batch_table]]` entry with N source files,
  concatenated in order. A file can't be both a single table and a
  batch member (discovery excludes batch members from the singles;
  the import/batch routes enforce it).
- Discovery (`core/discovery.py`) answers "what tables exist":
  `discover_for_history()` → `sources` (single) + `batch_tables`
  + the base config; `resolve_table_ref()` gives a normalized
  `TableRef { name, source_paths, is_batch, batch }`.

### 3.2 The IR: `TableIR` (`core/ir.py`)

What a reader produces and a writer consumes:

- `data` — the raw bytes (packed).
- `extra["fields"]` — structured multi-byte values (offset, width,
  value) used by writers for endianness-aware repacking and by the
  hex view's comments.
- `comments` — byte-offset annotations shown in the hex viewer.
- `byte_order`, `name`, `source_size`.

Writers that see `extra["fields"]` repack them honoring the
configured `byte_order`; a reader without fields passes bytes through
(`raw_text` → `bin` is a pass-through).

### 3.3 Config resolution (where a value comes from)

`resolve_table_config()` (discovery.py) merges, least to most specific:

```
global [defaults]  →  [[cluster]] defaults  →  sidecar (single tables)
                                                 or [[batch_table]] overrides
```

- A single table's overrides live in `<name>.config.toml` (the
  **sidecar**), written by `pld config set` / the webapp's per-row
  Settings modal.
- A batch's overrides (reader/writer/byte_order/**pipeline**) live
  inline in its `[[batch_table]]` entry (`effective_config`).
- Plugin options live in `[plugin.<name>]` (global or sidecar),
  replace-per-name (the submitted section is authoritative).

### 3.4 The build pipeline (`core/pipeline.py`)

1. `resolve_pipeline_spec(source_paths, config)` — explicit stages from
   the sidecar/`[[batch_table]]`, else the implicit
   `reader -> writer` pair.
2. `validate_pipeline_against_registry(spec, registry)` — every stage
   must resolve to a known plugin; exec stages are checked for
   available binaries.
3. `_run_pipeline` — reader → (exec stages) → writer, honoring
   `byte_order`, fan-out (multiple writers), and the **cache**
   (signature = source hash + effective config; a byte_order change is
   a cache miss).
4. Outputs land in `build/` with the table's name
   (`<table>.bin` / `.hex` / …).

### 3.5 History, golden, commit (`history.py`, `golden.py`)

- A **commit** snapshots the source blobs + output blobs + the
  effective config (including `byte_order`). `is_dirty()` compares
  source hashes, output hashes AND `byte_order` — a config-only change
  is committable even when the bytes come out identical.
- The **golden** is a chosen snapshot; `golden_status` reports
  match / mismatch / stale (stale wins: the source changed after the
  golden was set).
- Deleting a table keeps its history — `pld restore` / the web
  restore bring the source + output back from a snapshot.

### 3.6 Plugins (`plugin_base.py`, `registry.py`)

- Readers (`parse` → TableIR), Writers (`emit(ir)` → bytes), and
  `DoctorCheck`s. A plugin may declare more than one.
- The contract is validated at install time (`pld plugin install` /
  the web install modal) — `pld doctor` checks loadability.
- Resolution: `registry.resolve_reader(ext/name, config)`. The
  reader/writer for a table come from the sidecar/batch overrides,
  the cluster, or the global defaults, in that order.
- `config["table_meta"]` carries the table's tags/notes/properties to
  writers at build time (`TableIR` + the effective config).

---

## 4. The CLI (`cli.py`)

One file, every command a `@app.command()` with a `def _run()` body
wrapped in `run_command(_run, verbosity)`. Error handling: core
exceptions carry exit codes (see `errors.py`); `run_command` maps
`PayloadError` subclasses to their code and prints `✗ message`.

To add a command: write it in `cli.py`, then add a test in
`tests/test_cli_*.py`. Interactive scripts must accept `--yes`
(confirmation prompts abort in non-interactive runs).

---

## 5. The web app

### 5.1 Routes (`web/routes/`)

One module per area; each exports `ROUTES = [Route(...)]` registered
by `web/app.py`. They use Starlette + `anyio.to_thread` for blocking
work. Errors map to `InvalidRequestError` etc. → JSON
`{error, message, hint}`. Read-only routes are `GET`; mutations are
`POST`/`PUT`/`DELETE` with JSON bodies. SSE: `/api/build-all/stream`.

### 5.2 The SPA

- `app.js` — the hash router: `[/^\/table\/(.+)/, viewTable]` etc.;
  navigation clears dirty guards; the sidebar gear opens the Settings
  modal.
- `views/` — one module per page, each exporting `viewXxx()` (or
  `openXxxModal()` for the modal-only ones). The checker builds the
  module graph from `app.js` imports.
- `ui.js` — the helpers that matter:

  - `render\`...\`` — a tagged template that **escapes** interpolations
    and inlines `raw()` markers. `icon()` returns a raw marker.
  - `iconSpan()` returns a plain HTML string (use in PLAIN templates),
    `icon()` returns a raw marker (use inside `render`).
  - `openDialog`, `openTextEditorModal`, `openSettingsModal`,
    `confirmDialog`, toasts, the `render`/`raw`/`icon*` helpers, the
    dirty-guard API.

### 5.3 The template-context rule (read this before editing JS)

`iconSpan()` in a `render\`...\`` template is **escaped**; `icon()` or
`raw()` in a **plain** template stringifies as `[object Object]`. The
checker (`scripts/check_frontend_js.py`) flags both — it's how the
recurring "wrong helper" bug stays dead. Run it after any view edit:

```bash
python3 scripts/check_frontend_js.py
```

### 5.4 Modals over pages

The app favors modals over dedicated pages: Settings (gear), Build
all (dashboard), per-row Settings (table/batch), the pipeline editor,
the source editor, hex views, preview-compare. New "management" UI
should follow that pattern (a modal opened from where the action
starts), not a new sidebar page.

---

## 6. Where to touch to do X

| To do this… | …touch |
|---|---|
| Add a reader/writer/doctor check | `examples/plugins/` or a new plugin; the contract in `core/plugin_base.py`; register via `pld plugin install` |
| Add a CLI command | `cli.py` + a `tests/test_cli_*.py` test |
| Add a web route | `web/routes/<area>.py` (new module or existing) + register in `web/app.py` + a `tests/test_web_*.py` test |
| Change a table's build | `core/pipeline.py` (+ `pipeline_spec.py` for stage kinds) |
| Add a pipeline stage type | `core/pipeline_spec.py` (dataclass + parse + validate), the pipeline editor in `views/pipeline_editor.js`, the CLI `--stage` parser |
| Change config resolution | `core/discovery.py::resolve_table_config`, `core/batch_tables.py::effective_config` |
| Add a sidecar/batch/plugin config field | `core/config.py` (read + write helpers) + the Settings modal (`views/config.js`) |
| Change golden/commit semantics | `core/history.py`, `core/golden.py` (and the routes/CLI that call them) |
| Add a dashboard column/action | `views/dashboard.js` (the 5-column table) + `core/report.py` for the data |
| Add a table-page card | `views/table.js` (the pinned-card layout) + the route behind it |
| Change the Files page | `web/routes/fs.py` (backend) + `views/files.js` (tree/detail/modals) |
| Add a doctor check | a `DoctorCheck` subclass in the plugin + the ToolchainCheck pattern |
| Add a batch operation | `core/config.py` (upsert/add/remove) + `web/routes/batch.py` + the batch Settings modal |
| Change docs | `src/payload/docs/*.md` + `web/routes/docs.py` (the /docs listing) |
| Add a frontend page | a `views/<name>.js` + a route in `app.js` + a sidebar entry in `index.html` |

---

## 7. Testing

```bash
python3 -m pytest -q -p no:cacheprovider              # 1347 tests, 100% coverage
python3 scripts/check_frontend_js.py                  # JS imports + template context
bash tests/js_harness/run_harness.sh                  # the JS UI harness (jsc, in-repo)
```

- The pytest suite enforces **100% coverage** (a missing line fails the
  run) — add tests for every new branch.
- `tests/js_harness/` runs the UI modules under JavaScriptCore with
  stubbed browser APIs — the fast regression net for the SPA. When you
  change a view, extend `harness_files.mjs`.
- `tests/deep_testing/` (gitignored, local-only) has the heavy
  pre-release suites: a CLI scenario suite, a real-browser Playwright
  E2E suite, and a **visual/layout suite** (screenshots + pixel-diff
  baselines + overflow/overlap checks across viewports). It caught the
  last several responsive bugs.

---

## 8. Conventions

- Coverage stays at 100%; the JS harness stays green; `0 console
  errors` in the Playwright suite.
- JSON routes return `{error, message, hint}` on failure.
- Interactive CLI commands accept `--yes` for scripts.
- A file can't be both a single table and a batch member (enforced in
  the import/batch routes — never bypass).
- Plugin options are replace-per-name (the submitted `[plugin.<name>]`
  is authoritative).
- New UI = modals, not new sidebar pages; use the icon/render helpers
  per the template-context rule.
- Release: bump `version` in `pyproject.toml`, keep USAGE.md/HOWTO.md/
  DEVELOPER.md in sync, tag `vX.Y.Z`.
