# Changelog

## v0.6.0 (2026-08-03)

**Webapp rework** — the browser UI got a full pass: a filterable/sortable
dashboard, a proper table page, full-screen editors, and a report.

- Dashboard: compact table (fixed layout, no horizontal scroll) with
  status/golden/pipeline/size columns, inline filter (name/tag/cluster/
  note/property), column sorting, quick build + download.
- Table page: "Table info" card (cluster, tags, notes, custom
  properties), Build/Commit/History with snapshot detail modal + context
  menu (download/set golden/restore), paged hex view for binary sources,
  rename/clone table.
- Custom per-table properties (`pld meta`, `[[table_meta]] notes/
  properties`) — exposed to plugins at build time via
  `config['table_meta']`, so a reader can forward them to the writer
  through `TableIR.extra`.
- Files: light detail pane (no inline previews) — **Edit** opens a
  near-fullscreen CodeMirror modal, **View hex** a full-screen paged hex
  viewer; grouped action buttons (Content/Manage); full right-click
  context menu (edit/hex/analyze/compare/download/move/copy/delete);
  drag & drop clears the selection.
- Docs: cross-guide links navigate in-app (`#/docs/<slug>`), external
  URLs keep a new tab.
- Doctor: severity banner + Failures/Warnings/Passed sections (static —
  no collapsible boxes that resize the layout).
- New **Batch tables** page (`/batch`): create/edit/delete `[[batch_table]]`
  with a member picker; the picker shows only table-source candidates
  (`/api/batch/candidates` — config/sidecars/hidden/internal dirs excluded)
  and non-candidate members are rejected server-side.
- Dashboard: batch tables marked with a small accent icon (not a tag),
  uniform row heights with/without tags, fixed-width name column.
- Table page: Pipeline and sidecar cards are always open (no more
  collapsible boxes); batch marker icon.
- Keyboard shortcuts: `/` palette, `?` help, `g <key>` two-key navigation.
- Printable **HTML report** (`/api/report/html` and `pld report --html`):
  same content from web and CLI.
- Router: URL-decoded params (names with spaces/apostrophes), serialized
  navigation, modals closed on navigation.


**Project file browser + inspection tools** — the webapp can now manage
the whole project folder from the browser ("never touch the filesystem"),
and both the CLI and the web gained byte-level inspection commands.

**File browser** (web, `/files`):
- Tree with expand/collapse, table context badges (source/batch
  member/sidecar), multi-selection with batch move/copy/delete, drag &
  drop (move by default, modifier to copy, OS files dropped on a folder
  upload there), right-click context menu, text editor (CodeMirror),
  paged hex view (8+8 grouping, ASCII, Strings), full CRUD/upload.
- Safety: every path is checked against the project root (no traversal
  or symlink escape), and creating/renaming/uploading a file whose name
  would collide with an existing table is refused (table names must stay
  unique project-wide).

**Inspection commands** (CLI + web): `pld compare <a> <b>` (byte-level
diff), `pld grep <pattern> [--hex]` (content search), `pld analyze
<file>` (entropy/magic/frequency), `pld activity` (project-wide
timeline). In the web they are also available as buttons on the Files
page (Compare with a file picker, Search, Analyze) and on the table page
(`Diff vs snapshot`, `Diff vs golden`, `Analyze output`).

**Web UI**:
- Command palette (Ctrl/Cmd+K): search tables by name/tag, files, pages.
- Activity log page (`/log`).
- Redesigned Dashboard/table/Build-all/Clusters pages; dashboard table
  search moved into the palette.
- Activity events recorded in `.payload_activity/` for builds, commits,
  golden changes and file operations.
- Static files served with `Cache-Control: no-cache` (no stale frontend
  code after an update).

**Fixes**: CRLF files no longer look "edited" in the editors (normalized
guard), dashboard/status degrade with a warning on duplicate table
names instead of failing, icons and hex tables no longer render as
escaped text.

## v0.5.0 (not yet released)

**Clusters & tags** — for projects with more than a handful of tables. See
[src/payload/docs/CLUSTERS.md](src/payload/docs/CLUSTERS.md) for the full
design.

- New `[[cluster]]`/`[[table_meta]]` config sections: a cluster is a named
  bundle of `defaults`/`plugin` overrides a table can opt into (at most one
  per table), sitting as a new tier in config resolution — global
  `[defaults]` → cluster → sidecar (or a batch table's own inline overrides)
  → CLI flags. `[[table_meta]]` also carries free-form `tags` per table
  (multiple, purely organizational, no effect on builds).
- CLI: `pld cluster new/list/show/edit/delete/assign/unassign`, `pld tag`,
  `pld tags`, and `pld build-all --cluster <name>` (combines with
  `--filter`). `pld report`/`pld config show` reflect cluster/tags where
  relevant.
- Web: new "Clusters" page (create/edit/delete, member list), a "Tags &
  cluster" card on each table's page, and — on the Dashboard — a search box
  plus cluster/tag filter chips (multiple active tags combine with OR, a
  cluster filter is single-select), shown only once a project actually uses
  either.
- New routes: `GET/POST /api/clusters`, `PUT/DELETE /api/clusters/{name}`,
  `PUT /api/table/{name}/cluster`, `GET/PUT /api/table/{name}/tags`;
  `/api/report`/`/api/status` gained `cluster`/`tags` fields, and
  `/api/build-all/stream` gained a `cluster` filter param.
- Deleting a table drops its `[[table_meta]]` entry too, so a removed table
  never leaves an orphaned cluster/tag assignment behind.

**Fix: table discovery no longer requires an installed reader.**
`pld status`/`build-all`/`commit`/the web dashboard all discovered tables by
scanning the project for files whose extension a *reader* recognized — with
zero readers bundled by default (see below), a fresh project showed **no
tables at all**, not even `pld init`'s own `example_table.raw`, until a
matching plugin was installed. Discovery (`core/discovery.py`,
`is_table_candidate`) is now format-agnostic: every file counts as a table
unless it's obvious infrastructure (`table-tool.toml`, `*.config.toml`
sidecars, `output_dir`, `cache_dir`, `plugins/`, hidden files/dirs) —
building it is the only thing that still needs a matching reader
(`NoReaderFoundError` there, same as always). Also fixed, found in the same
pass: a relative `output_dir`/`cache_dir` was resolved against the *server
process's* cwd instead of the project root, so the exclusion silently never
matched whenever `pld serve`/`--root` pointed at a different folder — masked
until now by the reader-extension filter above, which usually filtered out
build output anyway. `pld watch`'s live-reload filter follows the same rule
(`payload/watch.py`).

**Install a plugin from the web UI** — `pld plugin install` was CLI-only;
the Plugins page in `pld serve` now has an "Install plugin" card with a
local-path/URL field and a drag&drop zone (`POST /api/plugin/install`,
`core/plugin_install.install_plugin_from_bytes` for the upload case). Same
no-silent-overwrite behavior as the CLI, with an overwrite-confirm dialog on
collision.

**Bulk import, no reader required at import time** — two follow-ups to the
no-bundled-plugins change below, both aimed at the same problem: a fresh
project starts with zero readers, so importing shouldn't be blocked on one
being installed yet.

- `pld import <files...> --each` (and the Dashboard's drag&drop, which now
  asks "one batch table or N separate ones?" when 2+ files are dropped)
  imports every file as its OWN standalone table, instead of forcing a
  choice between one file at a time or bundling everything into a single
  `[[batch_table]]`. A name collision with an already-tracked table doesn't
  abort the whole run — that file is skipped and reported
  (`N imported, M skipped`), the rest still go through, unless `--overwrite`
  is also passed.
- `pld import`/`/api/table/import` no longer require a reader for the
  file's extension to already be installed — import is just "copy this
  file into the project", nothing reads its content. A format nothing can
  read yet only becomes a problem at build time (`NoReaderFoundError`
  there), not at import.

**No bundled plugins, project-owned `plugins/`** — `payload` no longer ships
any reader/writer of its own; every project brings its own (breaking change,
no compatibility shim).

- Removed the `[toolchain]` core config section — `compiler`/`compiler_flags`
  and `objcopy`/`objcopy_target`/`objcopy_arch` are now owned by whichever
  plugin needs them, under `[plugin.c_source]`/`[plugin.obj]` respectively. A
  `table-tool.toml` with a leftover `[toolchain]` section now fails
  validation ("unknown section") — move the values under `[plugin.*]`.
- Removed the bundled `raw_text`/`csv`/`c_source` readers and
  `bin`/`hex`/`obj`/`header` writers from the installed package (no more
  `payload.readers`/`payload.writers` entry-points, see `pyproject.toml`).
  They're now reference implementations in
  [examples/plugins/](examples/plugins/), installable on demand.
- New command: `pld plugin install <source> [--as NAME] [--dest plugins]
  [--overwrite]` — installs a single-file plugin from a local path or a raw
  `.py` URL into a project's `plugins/` folder, refusing to silently
  overwrite an existing file (same "explicit consent" principle as `pld
  import`).
- Renamed the project-local plugin folder from `local_plugins/` to
  `plugins/` — an existing project must rename its folder (the
  `PAYLOAD_PLUGIN_PATH` env var and the `/api/local-plugins/*` web routes are
  unchanged).
- `registry.is_builtin()` → `is_installed()`: now reflects "loaded via a
  pip-installed entry_point" (any package, not just payload's own — since
  payload ships none) vs "loaded from a project's `plugins/` folder". The web
  UI's "builtin" badge/filter is now "installed (pip)".
- The entry_points loading mechanism itself (`payload.readers`/
  `payload.writers`/`payload.doctor_checks`) is unchanged and still works for
  a real pip-distributed plugin shared across projects — it's just empty by
  default now.

**Batch table follow-ups** — closes the two gaps `v0.4.0`'s batch
tables explicitly deferred, see
[src/payload/docs/BATCH.md](src/payload/docs/BATCH.md).

- `pld watch` rebuilds the whole batch table when a member file
  changes, instead of just flagging that live-reload doesn't apply
- `pld restore` recreates a batch table that was fully removed
  (`pld rm <name>` without `--member`): source files come back from
  history and the `[[batch_table]]` entry is re-added to
  `table-tool.toml` with the recorded reader/writer — an explicit
  multi-stage pipeline isn't reconstructed automatically, `pld
  restore` prints the recorded pipeline so it can be re-added by hand
- Reworked the "Plugins" page in `pld serve`'s web UI: a single
  filterable grid (kind + "show built-ins" toggle) instead of three
  independently-resizing columns, so the layout stays visually stable
  regardless of what's expanded

## v0.2.0

**Configurable pipeline** — a single model for every build, see
[src/payload/docs/PIPELINE.md](src/payload/docs/PIPELINE.md) for the full design.

- Three stage types: `reader` (file → data), `writer` (data → file),
  `exec` (file → file, shell command/host tool)
- A single execution engine (`core/pipeline.py`) for every build: a
  "simple" build (`--from`/`--to`) is internally an implicit 2-stage
  pipeline, no separate code path
- `[pipeline]` in `table-tool.toml`/sidecar to declare explicit
  multi-stage pipelines — the sidecar replaces the whole `stages`
  list, it doesn't merge it element by element
- Alternation rules validated **before** running any stage
  (`InvalidPipelineError`, exit 2): a reader always followed by a
  writer, pipeline of at least 2 stages, a final `exec` requires
  `output_extension`
- Reader/writer compatibility checked on **every** pair in the
  pipeline, not just the first
- Intermediate files in `tmp/` next to the source, cleaned up
  automatically — `--keep-intermediate` on `build`/`build-all` to
  inspect them
- `--dry-run` never runs `exec` stages (possible real side effects)
- `on_error = "warn"` for non-essential `exec` stages (doesn't block the build)
- Cache over the whole pipeline (`compute_pipeline_cache_key`): changing
  even a single stage correctly invalidates it
- New `doctor` check `pipeline_exec`: reports (informational, not
  blocking) how many `exec` stages are configured in the project —
  they run arbitrary code from config, see PIPELINE.md's Security section
- Verified with real `gcc`/`objcopy` and real shell commands (not just
  simulated); a real bug found and fixed during testing:
  `on_error="warn"` on the last stage left the fallback file inside
  `tmp/`, which was cleaned up right after — it's now copied to the
  expected final location before cleanup
- **Per-stage caching**: every non-final `writer`/`exec` stage
  persists its own output (outside `tmp/`) with a key based on the
  pipeline prefix up to that point — changing only the last stage no
  longer requires recompiling an expensive upstream `.c` file.
  `--force` bypasses these checkpoints too, not just the final cache.
  Real bug found during implementation: `c_source.py`/`obj_writer.py`
  used the same shared pipeline `tmp/` and deleted it at the end of
  parsing/emit, breaking later stages — fixed by giving each one a
  private subfolder (`tmp/c_source_scratch/`,
  `tmp/obj_writer_scratch/`)
- New `pld pipeline show <table>` command: shows the resolved pipeline
  (implicit or explicit) and which stages currently have a valid cache
  checkpoint

## v0.1.1

Rollback checkpoint before starting the "pipeline" feature. Six fixes
that came out of real use on an actual project (SPARC/RTEMS), all
verified with real toolchains where applicable.

- `run_command` now shows the failed command's stderr/stdout with `-vv` — previously the "run with -vv" promise was false, nothing was ever shown
- `readers/c_source.py` and `writers/obj_writer.py`: a local `tmp/` folder next to the source instead of `AppData\Local\Temp` (Windows) — created and cleaned up automatically on every build, never left dirty
- `.gitignore`: added `tmp/`
- Fix: `pld watch <subfolder>` never found the global config (`table-tool.toml`) if the watched subfolder didn't match the folder `pld` was launched from — the global config is now always looked up from `Path.cwd()`, consistent with `pld build`. The per-table sidecar was never affected (it's always resolved relative to the file, not to `root`)
- New `pld plugin new-local <name> --kind reader|writer|doctor-check` command: quick scaffold for a local plugin (single file in `local_plugins/`, no `pip install`) — previously the only scaffold available (`pld plugin new`) generated a whole pip package, overkill for a project-local plugin

## v0.1.0

First working version of the tool.

### Core pipeline
- Plugin architecture: `source → Reader → TableIR → Writer → output`
- Plugin discovery via `entry_points` (`payload.readers`, `payload.writers`, `payload.doctor_checks`)
- Incremental cache based on content hash (source + reader + writer + config)
- Automatic writer resolution: explicit `--to` → config → `reader.default_writer` → clear error
- `writer.compatible_readers`: incompatible reader/writer combinations rejected before parsing
- Explicit endianness handling (`TableIR.byte_order`, `extra["fields"]`, `payload.core.byteorder`)
- Passing extra information to plugins: persistent `[plugin.<name>]` in config, one-off `--opt key=value` from the CLI (both go into the cache key)
- Local plugins without `pip install`: `local_plugins/` next to the project or `PAYLOAD_PLUGIN_PATH`, `READER`/`WRITER`/`DOCTOR_CHECK` module-level convention (singular or plural)
- Dependencies declared by local plugins: `REQUIRES = [...]`, read statically (AST, not execution) even if the module wouldn't be importable; `pld plugin install-deps <file>` installs them with pip
- `pld init --wizard`: guided mode (project name, what to include, default writer/byte_order, optional `git init`); `local_plugins/` created by default even without the wizard
- `doctor`: new `git` (informational) and `local_plugin_deps` (missing dependencies in local plugins) checks; fixed two checks (`plugins`, `table_names`) that ignored the real project root
- Fix: `UnicodeEncodeError` on Windows consoles with legacy codepages (cp1252) while printing tips with emoji — `stdout`/`stderr` reconfigured with `errors="replace"` at CLI startup
- Fix (real cause, not the first hypothesis): inside a frozen PyInstaller exe, `importlib.metadata.entry_points()` doesn't find the builtin plugins even with `--copy-metadata payload` and even though `importlib.metadata.version()` works for the same package — the 6 builtin plugins are now registered with a direct `import` when `sys.frozen` is true (`core/builtin_plugins.py`), bypassing `entry_points` entirely in that context. No impact on a normal install (pip/wheel), verified it still uses `entry_points` as always
- `build-exe.yml`: the verification step now fails explicitly if the builtin plugins aren't found, instead of silently succeeding with an empty table
- Fix: `ModuleNotFoundError: payload.core.builtin_plugins` inside the exe — the nested lazy imports (load_plugins → builtin_plugins → individual readers/writers) weren't followed all the way by PyInstaller's static analysis. Added `--collect-submodules payload` to the build command, which bundles the whole package regardless of what static analysis can detect on its own

### Commands
- `init`, `doctor`, `plugins`, `plugin new/validate/info`, `clean`
- `build`, `build-all` (with real parallelism via `--jobs`, `ThreadPoolExecutor`)
- `watch` (debounce, automatic exclusion of the output dir)
- `view`, `golden update/check/diff`
- `status`, `commit`, `log`, `diff`, `restore` — lightweight per-table checkpointing, with deduplicated, sharded blob storage
- `config show` — resolved config with per-field provenance (default/global/sidecar)
- `report` — project overview (sizes, byte_order, golden status, latest snapshot)
- `export` — portable `.zip` archive of sources + project config

### Included plugins
- Readers: `raw_text` (text with comments), `csv` (structured, multi-byte, endianness), `c_source` (compiles a real `.c` file, extracts bytes from a dedicated section)
- Writers: `bin` (raw dump, with automatic repacking on endianness mismatch), `hex` (Intel HEX), `obj` (linkable `.o`, per-table named section, `__start_`/`__stop_` symbols verified with a real link)

### Testing and packaging
- CLI-level tests (`tests/test_cli_smoke.py`, `CliRunner`) in addition to core-level ones
- `tests/test_c_source_and_obj.py` — verified with real `gcc`/`objcopy`, including a full C link that reads the data through the `__start_`/`__stop_` symbols generated by the linker
- `pytest-cov` configured (report, no threshold enforced a priori)
- Verified the real wheel build (`py3-none-any`, no compiled dependency) and a non-editable install in a clean venv, entry_points inspected directly from the installed package
- `.github/workflows/build-exe.yml` — automatic build of a standalone `pld.exe` (Windows, PyInstaller) on `v*` tags, attached to the GitHub Release. The builtin plugins work inside the exe (`--copy-metadata payload`); local plugins (`local_plugins/`, `PAYLOAD_PLUGIN_PATH`) work identically, no recompilation needed — **not verified with a real build** (requires a Windows runner, not available in this development environment)

### Robustness
- Exception hierarchy with dedicated exit codes (0-5) and consistent log levels
- 3-tier config (global → per-table sidecar → CLI) validated by hand, **zero compiled dependencies**
  (removed `pydantic`: its Rust extension doesn't install on several ARM/Termux platforms)
- Duplicate table name detection (`build-all`, `doctor`) — build/golden/history are indexed by name
- `pld init` never writes to the current folder without explicit confirmation
- Fix: `typer.Exit` raised inside `_run()` (11 commands: doctor, clean, view, diff, restore, etc.) was incorrectly caught as an internal bug instead of a controlled exit
- Conformance suite (`payload.testing`) to validate third-party plugins at runtime, without requiring pytest

### Documentation
- `src/payload/docs/USAGE.md` — complete user guide
- `src/payload/docs/PLUGINS.md` — plugin developer guide, including sections on endianness and the reader/writer relationship
- Docstring on every plugin class, shown by `pld plugin info <name>`

### Known gaps, not yet done
- No automated tests on real Windows/macOS (code audit only) — not a priority for this release
- `pld watch` on a single file hasn't been validated on Android/Termux
- No test coverage threshold set (measured, not yet decided)
- `tests/test_cli_smoke.py` (including the `init` wizard) written but not run in this repository's development environment (no access to `typer` there) — carefully verified against the documented API, to be confirmed with a real run
- `pld.exe` built in CI but with the entry_points bug just described — the fix (direct import of builtins when frozen) hasn't been verified on a real Windows build yet, only simulated with `sys.frozen = True` in this development environment
- `pld plugin install-deps` doesn't work inside a frozen `pld.exe` (no real Python interpreter behind `sys.executable` there) — documented as a known limitation, not a bug to fix
