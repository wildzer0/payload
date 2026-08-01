# Changelog

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

## v0.1.0 (not yet released)

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
