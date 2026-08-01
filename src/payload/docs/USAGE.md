# User guide — `pld`

Complete reference for every command, the configuration file, and exit
codes. To write a plugin (developer side), see instead
[PLUGINS.md](PLUGINS.md).

## Installation

```bash
pip install -e ".[dev]"    # from the project root
pld --version
```

### Isolated environment

A dedicated `venv` is recommended (stdlib, zero extra dependencies):

```bash
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Alternatively, if you want `pld` as a global command (not tied to a
single Python project), **`pipx`** is built exactly for this: it
creates an isolated venv automatically and only exposes the `pld`
command on the PATH.

### Corporate environments/closed network

`payload` has no compiled dependency (see the Compatibility section
further down), so its wheels are "universal" — the exact same wheel
works everywhere, no compilation required even in a fully offline
environment. In order of preference:

1. **Internal pip index** (Artifactory, Nexus, devpi): `pip install
   --index-url https://pypi.yourcompany.com/simple payload` — identical
   to normal use.
2. **Offline wheelhouse**: from a machine with internet access, `pip
   download payload -d ./wheelhouse` downloads everything (dependencies
   included); transfer the folder to the closed network and install with
   `pip install --no-index --find-links=./wheelhouse -e .`.
3. **Internal git**: `pip install git+https://git.yourcompany.com/team/payload.git`
   if the git server is reachable from the closed network.

The same three methods work for installing a third-party plugin
(`pld plugin new` generates a pip package like any other). For a
plugin specific to a single project, see also "Local plugins" in
[PLUGINS.md](PLUGINS.md#local-plugins-without-pip-install) — it
requires no installation at all.

## Concepts in brief

```
source (.raw | .csv | ...) → [Reader] → TableIR → [Writer] → output (.bin | .hex | ...)
```

Every table is an independent source file. The **reader** is chosen by
extension (or explicitly with `--from`); the destination **writer** is
chosen with `--to` (or from the default in config). A **hash-based
cache** avoids rebuilding tables whose source/config hasn't changed;
**golden** lets you detect whether the output has changed relative to
a historical snapshot marked as the reference (see `pld
commit`/`pld golden set` further down — golden isn't a separate file,
it's a pointer to an already-saved snapshot).

---

## Commands

### `pld init [name] [--force] [--wizard/-w] [--yes/-y]`

Creates the minimal scaffold of a project: `table-tool.toml`, the
`build/` folder and `local_plugins/` (for external plugins without
`pip install`, see [PLUGINS.md](PLUGINS.md#local-plugins-without-pip-install)),
and a sample table (`example_table.raw`).

```bash
pld init my-project       # creates a new dedicated folder (recommended)
pld init                  # in the current folder, with confirmation if not empty
pld init --force          # overwrites existing files
pld init --wizard         # guided mode, asks what to include
```

**With a name**, it creates a new folder with that name and puts the
scaffold inside it — you can't accidentally end up with files scattered
elsewhere. **Without a name**, if the current folder isn't empty, it
asks for explicit confirmation before writing anything; if you decline,
it touches nothing and suggests using `pld init <name>`.

**With `--wizard`**, it walks you step by step through the choices
instead of using all the defaults: project name (if not already given
as an argument), whether to include `local_plugins/`, whether to
include the sample table, default writer, default `byte_order`,
whether to initialize a git repository (`git init`, only if `git` is on
the PATH). `--yes` combined with `--wizard` skips every question and
uses the defaults — useful for scripts/automation that still want the
wizard's "complete" scaffold without interaction.

Every project always has a **name** (`[project]` section in
`table-tool.toml`, shown on the web Dashboard) — with `pld init <name>`
it's that name, without an argument it's the name of the folder you're
initializing in (never a reason to refuse creating the project).
Changeable at any time by editing `[project] name = "..."` by hand.

### `pld build <source|batch-table-name> [options]`

Builds a single table. Internally it always runs a **pipeline** —
implicit, 2 stages (`--from`/`--to`, the common case), or explicit if
the table has a `[pipeline]` section in config, see
[PIPELINE.md](PIPELINE.md).

The first argument is usually a file path. If it doesn't exist as a
file, it's looked up by name among the batch tables declared in
`[[batch_table]]` (see [BATCH.md](BATCH.md)) — a logical table built
from several source files instead of one:

```bash
pld build sensors/rows.txt --to bin   # single file
pld build rows --to bin               # name of a [[batch_table]]
```

| Option | Default | Meaning |
|---|---|---|
| `--from <reader>` | auto-detect | forces a specific reader instead of auto-detecting by extension |
| `--to <writer>` | from config | output writer to use |
| `--out <dir>` | `build` | output folder |
| `--force` | off | ignores the cache, rebuilds anyway |
| `--dry-run` | off | shows what would happen without writing anything (never runs `exec` stages) |
| `--check-golden` | off | fails (exit 3) if the golden status isn't `match` (mismatch or stale) |
| `--opt key=value` | — | one-off override for the active plugin, repeatable (see [PLUGINS.md](PLUGINS.md#passing-extra-information-to-a-plugin)) |
| `--keep-intermediate` | off | doesn't clean up `tmp/` after the build — useful for inspecting the intermediate files of a multi-stage pipeline |

```bash
pld build sensors/temp_table.raw --to bin
pld build sensors/temp_table.raw --from raw_text --to hex --out release/
```

**Note**: `--from`/`--to` are **ignored** (with a warning) if the table
already has an explicit `[pipeline]` in config — the two don't mix, the
explicit pipeline already declares everything it needs.

### `pld build-all [root] [options]`

Recursive batch build over every table found under `root` (default
`.`). Tables are independent of each other: no dependency graph, so
the batch can be safely parallelized.

| Option | Default | Meaning |
|---|---|---|
| `--to <writer>` | from config | writer to use for every table found |
| `--out <dir>` | `build` | output folder |
| `--jobs N` | `1` | degree of parallelism (thread pool) |
| `--filter <glob>` | all known sources | limits the scan, e.g. `"sensors/**"` |
| `--force`, `--dry-run`, `--check-golden` | same as `build` | applied to every table |
| `--opt key=value` | — | one-off override applied to every table in the batch, repeatable |
| `--keep-intermediate` | off | doesn't clean up `tmp/` after each build in the batch |

```bash
pld build-all . --to bin --jobs 4
pld build-all . --filter "sensors/**" --check-golden
```

At the end it shows a summary: how many tables built, how many served
from cache, how many with a golden mismatch, how many errors. If there
are errors, it exits with `BatchBuildError` (exit 1) but **it has still
attempted every table**, it doesn't stop at the first failure.

### `pld watch [file|root] [--to <writer>] [--out <dir>]`

Automatic rebuild on every save, with debounce (groups events happening
close together on the same file, e.g. an editor's write+rename).
`Ctrl+C` to exit. A build error in watch mode doesn't stop the watch:
it's logged and watching continues.

```bash
pld watch sensors/temp_table.raw --to bin
pld watch .   # watches the whole folder recursively
```

### `pld config show [table] [--root <dir>]`

Shows the resolved config (3 tiers: default → global → sidecar) and
**where each value comes from** — useful when it's not obvious which
tier is winning for a specific table.

```bash
pld config show                # global config, no sidecar involved
pld config show temp_table     # includes this table's sidecar, if any
```

### `pld pipeline show <table> [--root <dir>]`

Shows the resolved pipeline for a table (implicit, 2 stages from
`--from`/`--to`, or explicit from `[pipeline]` in config) — see
[PIPELINE.md](PIPELINE.md). Also shows which stages currently have a
valid cache checkpoint.

```bash
pld pipeline show temp_table
```

### `pld report [root]`

Project overview: one line per table with source size, output size (or
"never built"), `byte_order`, golden status, latest history snapshot.
Useful before sharing or archiving a project, or for a general
at-a-glance view.

```bash
pld report
```

### `pld export <output.zip> [--include-history] [root]`

Creates a portable `.zip` archive with every table source discovered,
`table-tool.toml`, and every `.config.toml` sidecar found — useful for
sharing a sub-project or backing it up outside of git.

```bash
pld export backup.zip
pld export backup.zip --include-history   # also includes .payload_history/
```

To download just the **already-built output** of a single table (not
the whole project, no commit needed either) — a web-only concept, no
CLI equivalent: `GET /api/table/<name>/download`. A single output file
is served directly (the common case); several files (multi-writer
fan-out) are zipped on the fly, always and only the current outputs,
never the source. On the Dashboard it's the download button on every
table card (visible only if an output already exists), the same button
also appears on the table detail page. To instead download a
**historical snapshot** (source + output of a past commit), see the
"Download" button on every table's History page — that one requires a
commit to already exist and is what `pld log`/`pld diff`/`pld restore`
consult.

### `pld status [root]`

Shows which tables have changed relative to the last saved snapshot
(`never saved` / `changed` / `unchanged`).

```bash
pld status
```

### `pld commit -m "message" [--only <table>] [--golden] [root]`

Saves a snapshot of **source + generated output** for every changed
table (or only for the ones given with `--only`, repeatable). Unlike
git there's no separate `add` step: tables are independent of each
other, so there's nothing to stage together — each `commit` captures
everything that changed on its own. "Changed" now also considers the
output, not just the source: changing writer without touching the
source still produces something to commit.

With `--golden`, the snapshot just created also becomes the golden
reference for every committed table — collapses "build → commit → set
as golden" into a single command.

If the state you're committing comes from a fan-out pipeline that
partially failed (see PIPELINE.md, Fan-out section) — i.e. one or more
writers in the group didn't produce their output — the command prints
an explicit warning (`incomplete pipeline: missing <file>`) and the
snapshot remembers it forever (see `pld log` below): there's no way to
accidentally commit a partial state without noticing, neither now nor
later when reviewing the history.

**`commit` never builds anything on its own** — it commits the current
source plus whatever output is already present in `build/`. If a
changed table has **no** output at all (not a partial fan-out like
above, literally zero files: almost always a sign of forgetting
`pld build` first), that table is **skipped** with a warning (`skipped,
no output found`) instead of creating a useless snapshot with nothing
to attach — the other tables in the same `commit` are still saved
normally. Only if **no** dirty table has output to attach does the
command fail entirely (exit code 5).

```bash
pld commit -m "updated sensor calibration"
pld commit -m "just this table" --only temp_table
pld commit -m "output verified" --golden
```

### `pld log [table] [--root <dir>]`

Snapshot history, like `git log`. Without an argument, shows every
table ever saved. Each line also shows the attached outputs and what
produced them: the writer is inferred from the **extension of the
files actually committed** (accurate even with an ad-hoc `--to`/`--from`
override that was never written to config), the reader stays a
best-effort resolution from the config at commit time. The line is
marked `● current` if it's the snapshot the table is currently on (the
tip, by default, or an earlier snapshot after a `pld restore`), and is
flagged with a warning if it came from a partial fan-out pipeline (see
`pld commit` above).

```bash
pld log temp_table
pld log                # every tracked table
```

### `pld diff <table> [--snapshot <N>] [--root <dir>]`

Compares the current **source** against a snapshot (the latest, if
`--snapshot` is omitted), byte by byte — to compare the **output**
against the golden reference, see `pld golden diff`.

```bash
pld diff temp_table
pld diff temp_table --snapshot 2
```

### `pld restore <table> [N] [--root <dir>] [--yes]`

Brings **source and generated output** back to the state of snapshot
`N` and moves the "current" pointer (head) to that snapshot — `git
checkout <commit>` style: no new snapshot gets created, the history
stays intact and unchanged (later snapshots stay right there, browsable
and re-downloadable, they just aren't the "current" ones anymore until
a new `pld commit` comes in, which becomes the new tip regardless). If a
more recent snapshot had output from a different writer, those orphaned
files get removed from disk (`git checkout` style, not left lying
around). `N` is optional: if omitted, it uses the latest snapshot —
handy for undoing an accidental `pld rm` without first having to check
`pld log`. Asks for confirmation unless `--yes`.

```bash
pld restore temp_table 3
pld restore temp_table          # latest snapshot
```

**Restoring after a deletion**: if the source is no longer on disk
(deleted with `pld rm`, or by hand) and it's a **single-file** table,
`pld restore` recreates it from scratch at the location it lived in at
commit time — the file doesn't need to already exist. Entirely deleted
**batch** tables (see `pld rm` below) aren't restorable this way: their
`[[batch_table]]` declaration needs to be recreated by hand in
`table-tool.toml` before the individual member files can be restored.

**Note**: this doesn't replace git for the project as a whole — `build/`
is typically excluded from git (it's an artifact), so git alone never
ties together "what the source looked like" and "what the generated
binary looked like" at the same instant. This system exists precisely
to fill that gap, with deduplicated storage in `.payload_history/`
(identical content across snapshots doesn't take up double space). If
you want these snapshots to also be safe on a remote, `.payload_history/`
can happily live inside the project's git repo.

### `pld rm <table> [--member <file>] --force [--yes] [--root <dir>]`

Deletes a table's **source(s) + output + cache** — never the history:
snapshots remain browsable with `pld log`, and for a single-file table
also restorable with `pld restore` (see above).

`--force` is mandatory: without it, the command refuses immediately, as
a safety net against a typo in the table name. Even with `--force`, it
always asks for explicit confirmation (unless `--yes`, for
scripts/CI) — showing what it's about to delete, and whether the table
has uncommitted changes that would be lost forever.

```bash
pld rm old_sensor --force
```

On a **batch table**, without `--member` it deletes every member file
AND the entire `[[batch_table]]` declaration from `table-tool.toml` (a
batch table with 0 files makes no sense). With `--member <file>`, it
deletes only that file: if others remain, the batch declaration gets
updated (the path removed from `sources`, if it was listed explicitly
— a path matched by a glob pattern needs no change, disappearing from
disk is already enough); if it was the last one, the `[[batch_table]]`
gets removed anyway.

```bash
pld rm sensors --member ROW3.txt --force
```

### `pld import <file...> [--as <name>] [--batch <name>] [--new-batch <name>] [--overwrite] [--root <dir>]`

Copies one or more external files **into the project** as a new table
(or updates the source of one already tracked, with `--overwrite`) —
the location is always the project root, decided by the tool: you no
longer need to organize folders by hand. Rejects a file whose extension
no reader recognizes, or that's empty (0 bytes — almost always a wrong
file or an interrupted upload), before even copying it.

```bash
# new single-file table (name derived from the file, without its extension)
pld import ~/Downloads/new_sensor.raw

# explicit name
pld import ~/Downloads/data.raw --as external_temp

# update the source of an already tracked table
pld import ~/Downloads/data_v2.raw --as external_temp --overwrite

# new batch table from several files together
pld import ROW1.txt ROW2.txt ROW3.txt --new-batch sensors

# adds a member to an already declared batch table
pld import ROW4.txt --batch sensors
```

`--batch`/`--new-batch` are mutually exclusive; more than one file
together requires `--new-batch` (otherwise only one file at a time).
Same mechanism on the web side: the Dashboard has a drag&drop zone that
covers the same cases (a single file asks for a name, several files
together ask for the batch table's name).

### `pld view <source> [--from <reader>]`

Inspects the raw content (hexadecimal bytes + any comments) of a
table, without writing any output.

```bash
pld view sensors/temp_table.raw
```

### `pld golden set <table> [--snapshot N]`

Sets which **already-saved snapshot** (see `pld commit`) is the golden
reference for a table — not a separate frozen file, a pointer: that
snapshot's source and output ARE the golden. Without `--snapshot`, uses
the latest one. Requires at least one existing snapshot (`pld commit`
first).

```bash
pld golden set sensors_temp_table              # latest snapshot
pld golden set sensors_temp_table --snapshot 3 # a specific one
pld commit -m "verified output" --golden       # commit + set golden in one shot
```

### `pld golden check [table]`

Checks a table's golden status, or every table's if omitted. Four
possible states: `match` (source and output match the snapshot),
`mismatch` (source unchanged, output different — a real regression),
`stale` (the *source* changed after the golden was set, so comparing
the output is no longer reliable), `missing` (no golden set). Exit 3
if `mismatch`/`stale` (on a single table) or if any table isn't at
`match` (checking all).

### `pld golden diff <table>`

Byte-by-byte differences between the current output and the golden
snapshot.

### `pld golden clear <table>`

Removes a table's golden reference. Snapshots stay intact, only the
pointer is removed.

### `pld doctor`

Pre-flight check: toolchain reachable, plugins loadable (with the
names of any broken ones), config valid (global + every sidecar),
duplicate table names, local plugin dependencies satisfied, git
available (informational, non-blocking), how many `exec` stages are
configured in the project (informational — see
[PIPELINE.md](PIPELINE.md#security--dont-underestimate-this)), writable
directories, cache not corrupted. Run it before a big batch or in CI.
Exit 2 if at least one check fails (FAIL) — a `WARN` check (e.g. git
missing, a local plugin with missing dependencies, or `exec` stages
configured) doesn't affect the exit code.

```bash
pld doctor
```

### `pld plugins`

Lists registered readers/writers/doctor-checks, with extensions and
supported API version.

### `pld plugin info <name>`

Shows a specific plugin's documentation: its class's docstring (should
explain the format it handles, with an example), supported extensions,
suggested writer, any compatibility constraints. This is a plugin's
"user" documentation — to write a new one, see instead
[PLUGINS.md](PLUGINS.md).

```bash
pld plugin info csv
```

### `pld plugin install-deps <file.py> [--yes]`

Installs, with `pip`, the dependencies declared by a **local plugin**
(module-level `REQUIRES = [...]`, see
[PLUGINS.md](PLUGINS.md#local-plugins-without-pip-install)). Not related
to a plugin installed via pip — that one already manages its own
dependencies through its `pyproject.toml`.

```bash
pld plugin install-deps local_plugins/my_writer.py
```

### `pld plugin new <package-name> --kind reader|writer|doctor-check [--dest <dir>]`

Generates the scaffold for a new installable plugin. See [PLUGINS.md](PLUGINS.md).

```bash
pld plugin new payload-writer-hex --kind writer
```

### `pld plugin validate <name> [--sample <file>]`

Checks that an already-installed plugin honors the Reader/Writer
contract, at runtime. See [PLUGINS.md](PLUGINS.md#validating-that-the-plugin-really-honors-the-contract).

```bash
pld plugin validate csv --sample sample.csv
```

### `pld clean [--target cache|build|golden|all] [--yes]`

Empties the cache, build output, or (`golden`) the golden references
of every table — not a folder to delete, the pointers are removed from
`.payload_history/`, the snapshots stay. Asks for confirmation unless
`--yes`.

```bash
pld clean --target cache
pld clean --target all --yes
```

### `pld --version`

Shows the installed version.

### Verbosity: `-v`, `-vv`

Applicable to any command, before the subcommand:

```bash
pld -v build sensors/temp_table.raw --to bin     # INFO: main steps
pld -vv build sensors/temp_table.raw --to bin    # DEBUG: parse/emit timing, cache/config details
```

Logs go to `stderr`, never `stdout` — you can always pipe the command's
"real" output without logs polluting it.

---

## Configuration file

Three tiers, increasing precedence: **`table-tool.toml`** (global,
project root) → **per-table sidecar** (`<name>.config.toml` next to the
source) → **CLI flags**. The merge is deep: the sidecar only overwrites
the keys it explicitly declares.

`table-tool.toml`:

```toml
[defaults]
writer = "bin"              # writer used when --to isn't specified
reader = "raw_text"         # reader used when --from isn't specified (auto-detect if absent)
output_dir = "build"
cache_dir = ".payload_cache"
byte_order = "little"       # "little" | "big" — target for readers/writers that handle multi-byte values

[toolchain]
compiler = "gcc"
compiler_flags = []
objcopy = "objcopy"
objcopy_target = ""   # only required by the 'obj' writer, e.g. "elf32-littlearm"
objcopy_arch = ""     # only required by the 'obj' writer, e.g. "arm"
```

Sidecar `sensors/temp_table.config.toml` (optional, override only):

```toml
[defaults]
writer = "hex"               # only this table uses hex instead of the global default
```

A third section, `[plugin.<name>]`, is reserved for plugin-specific
information (not validated by the core) — see
[PLUGINS.md](PLUGINS.md#passing-extra-information-to-a-plugin).

A fourth, `[pipeline]`, declares an explicit pipeline for the table
(instead of the implicit reader/writer pair from `--from`/`--to`) —
see [PIPELINE.md](PIPELINE.md) for the full design, with examples.

A fifth, `[[batch_table]]` (only in the global `table-tool.toml`, never
in a sidecar), declares a logical table built from **several source
files** instead of one — see [BATCH.md](BATCH.md) for the full design,
with examples. It's written by hand, but `pld import
--new-batch`/`--batch` and `pld rm --member` also create/modify it on
their own (see above) — editing the TOML is no longer mandatory for
these common operations.

---

## Exit codes

| Code | Category | Example |
|---|---|---|
| `0` | success | build completed, doctor with no FAIL |
| `1` | build error | parsing failed, toolchain failed, batch with failures |
| `2` | config/plugin | malformed config, plugin not loadable, doctor with a FAIL |
| `3` | golden mismatch | `--check-golden` or `golden check` with differences |
| `4` | not found | nonexistent source/reader/writer/plugin |
| `5` | history | nonexistent snapshot, nothing to save |

Useful for external scripts and CI: `pld build-all . --check-golden || echo "regression detected"`.

---

## Compatibility

The core (`payload.core.*`) uses no OS-specific API: `pathlib`
everywhere instead of hand-built strings with `/`, `shutil.which`/
`subprocess.run` (without `shell=True`) to invoke external toolchains,
no POSIX-only module (`fcntl`, `pwd`, etc.). The only three
dependencies (`typer`, `rich`, `watchdog`) are pure Python or have
native backends for each OS handled automatically by watchdog (inotify
on Linux, FSEvents on macOS, ReadDirectoryChanges on Windows).

**No compiled dependency**: `pydantic` was deliberately removed (it
was the only one) because its Rust extension (`pydantic-core`) has no
precompiled wheel for several ARM platforms (e.g. Termux on Android) —
with only pure-Python dependencies, `pip install -e .` works anywhere
there's a Python 3.10+ interpreter, with no need for a compilation
toolchain on the host.

**Not automatically tested on every OS at this time** — the
cross-platform correctness described above is based on a code audit
(no OS-specific API), not a real run of the test suite on
Windows/macOS. If you use it on an OS other than Linux and hit a
problem, it's useful to know about it.

A known and handled detail: `Path.glob("folder/**")` on its own, on
any OS, only matches the folder and not the files inside it (a
pathlib behavior, not an OS one) — `--filter` normalizes this case
automatically.

## Standalone `.exe` distribution (Windows)

For those who don't want or can't install Python or `pip` (or want to
distribute `pld` to colleagues without explaining `pip install`),
there's a self-contained build made with
[PyInstaller](https://pyinstaller.org): a single `pld.exe` with Python
and all dependencies already bundled in.

**How to get it**: there's no automated CI build for this anymore — build
it locally (needs Windows, PyInstaller doesn't cross-compile):
```bash
pip install -e ".[build]"
pyinstaller --onefile --name pld --copy-metadata payload ^
    --hidden-import watchdog.observers.read_directory_changes ^
    scripts/pld_entry.py
```

### How plugins work with the exe

The **builtin plugins** (`raw_text`, `csv`, `c_source`, `bin`, `hex`,
`obj`) are already inside the exe, they work right away — they require
`--copy-metadata payload` at build time (already included in the
command above) because they're discovered via `entry_points`, which
needs the package's metadata even inside a frozen binary.

**External plugins** (third-party `.py` files, without recompiling the
exe) work through the same "local plugins" mechanism already covered
in [PLUGINS.md](PLUGINS.md#local-plugins-without-pip-install) —
`local_plugins/` next to wherever you launch `pld.exe` from, or
`PAYLOAD_PLUGIN_PATH`. It works because that mechanism loads `.py`
files from disk at runtime (`importlib.util`), an operation independent
of how the Python process itself was packaged — **no difference
compared to a normal installation**.

**Real limitation worth knowing**: a local plugin used with the exe can
only import the standard library and modules already bundled in the
exe (`payload.*`, `typer`, `rich`, `watchdog`) — if your plugin needs a
third-party library not included in the build (e.g. `numpy`), it won't
find it, because PyInstaller only includes what it statically detects
during the build. With a normal `pip install` this problem doesn't
exist (the Python environment has access to everything you install).

For the same reason, **`pld plugin install-deps` doesn't work inside
the frozen exe**: that command invokes `pip` through the current Python
interpreter, but inside a PyInstaller binary there's no real Python
interpreter behind the scenes to use for installing packages — if a
local plugin declares `REQUIRES`, with the exe that dependency either
needs to already be present in the exe (i.e. bundled at build time) or
the plugin simply won't work. If you need plugins with dynamic
dependencies, a normal `pip` installation remains the right choice.

## Typical end-to-end workflow

```bash
pld init                                    # initial scaffold
pld doctor                                  # checks that everything's in order
pld build example_table.raw --to bin        # first build
pld commit -m "verified output" --golden    # snapshot + freeze as the reference
# ... you change the tool or the plugin ...
pld build-all . --check-golden --jobs 4     # verify nothing broke
```
