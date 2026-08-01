# Writing a plugin for payload

This guide answers the question everyone asks the first time:
**what exactly must a reader do? what does a writer receive? do they
both know the same thing?**

## The contract in one line

```
source (any format) → Reader.parse() → TableIR → Writer.emit() → output file
```

**Yes, both the reader and the writer know `TableIR`.** It's the only
object that puts them in communication. The reader knows nothing about
the final output format; the writer knows nothing (and shouldn't know
anything) about how the data was read. This decoupling is the whole
point of the plugin system: a new reader automatically works with
every existing writer, and vice versa.

`TableIR` is defined in `payload/core/ir.py`:

```python
@dataclass
class TableIR:
    name: str                      # table name (e.g. from the filename)
    data: bytes                    # raw payload — THIS is the table's real content
    source_path: Path              # origin file, for cache/debug/errors
    source_format: str             # name of the reader that produced it

    comments: list[tuple[int, str]] = field(default_factory=list)  # (offset, text) — 'pld view' only
    extra: dict = field(default_factory=dict)  # future extensions, empty for now
```

| Field | Required | What it's for |
|---|---|---|
| `name` | yes | output file name (`{name}{writer.extension}`) |
| `data` | yes | **the content every writer serializes** — the heart of the IR |
| `source_path` | yes | used for error messages and as part of the cache key |
| `source_format` | yes | used in the cache key (different reader → cache invalidated) |
| `byte_order` | no (default `"little"`) | order `data` is already packed in — see the dedicated section below |
| `comments` | no | only `pld view` shows them; a writer can safely ignore them |
| `extra` | no | escape hatch for future metadata; `extra["fields"]` convention for endianness (below) |

---

## What a Reader must do

A reader **reads a file and returns a `TableIR`**. That's it. It
doesn't decide the output format, it doesn't write anything to disk.

Required interface (from `payload/core/plugin_base.py`):

```python
class Reader(Protocol):
    name: str              # unique identifier, used with --from
    extensions: list[str]  # e.g. [".csv"] — for auto-detection
    api_version: str       # = PLUGIN_API_VERSION (from payload.core.ir)

    def sniff(self, path: Path) -> bool:
        """Fallback for ambiguity: several readers with the same
        extension? The core calls sniff() on each and uses the one
        that returns True. For a simple case, just 'return False' —
        the extension alone is already enough to match you when
        there's no ambiguity."""
        ...

    def parse(self, path: Path, config: dict) -> TableIR:
        """The only method that really matters. Reads path, returns TableIR."""
        ...
```

**What happens if the file is malformed?** Raise `ReaderParseError`
(from `payload.core.errors`), never a generic `Exception` — that's
what guarantees a consistent error format in the CLI regardless of who
wrote the plugin:

```python
from payload.core.errors import ReaderParseError

raise ReaderParseError(path, "line 12: value out of range")
```

### Real example: `readers/csv_reader.py`

```python
class CsvReader:
    name = "csv"
    extensions = [".csv"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        # only used if another reader claimed the .csv extension too —
        # here we check that the header contains 'value'
        head = path.read_text(errors="ignore").splitlines()[:1]
        return bool(head) and "value" in head[0].lower()

    def parse(self, path: Path, config: dict) -> TableIR:
        data = bytearray()
        comments = []
        with path.open(newline="") as f:
            for row_num, row in enumerate(csv.DictReader(f), start=2):
                value = int(row["value"], 0)   # accepts '0x0A' or '10'
                data.append(value)
                if row.get("comment"):
                    comments.append((len(data) - 1, row["comment"]))
        return TableIR(
            name=path.stem, data=bytes(data),
            source_path=path, source_format=self.name, comments=comments,
        )
```

CSV format this reader expects:
```csv
value,comment
0x0A,min threshold
0x1B,
0x2C,max threshold
```

See the full file at `src/payload/readers/csv_reader.py` — it also
handles an optional `offset` column for non-contiguous data, and is a
good starting point to copy for a new reader.

### Optional extension: `parse_many` (batch tables)

A reader can *additionally* implement `parse_many(self, paths: list[Path], config: dict) -> TableIR`
to be usable in a **batch table** — a logical table built from several
source files instead of one (see [BATCH.md](BATCH.md) for the full
design). `parse()` stays mandatory and unchanged; `parse_many` is
detected via duck-typing (`getattr(reader, "parse_many", None)`), so a
reader that doesn't implement it keeps working exactly as it does
today — it just isn't usable in a `[[batch_table]]`.

```python
def parse_many(self, paths: list[Path], config: dict) -> TableIR:
    """paths is already in the correct concatenation order (decided by
    the caller, not the reader) — usually it's enough to reuse the
    same per-file logic as parse(), iterating over paths."""
    ...
```

**Don't provide an automatic fallback that blindly concatenates
`path.read_bytes()` for readers that don't implement `parse_many`**:
that's only correct for purely line-by-line/byte-by-byte formats (e.g.
`raw_text`, which does implement `parse_many`), wrong for any format
with a header or non-repeatable structure (e.g. binaries with a length
prefix) — a reader that can't handle the multi-file case should say so
clearly (`ReaderBatchUnsupportedError`, raised automatically by
`core/pipeline.py::build()`), not silently produce wrong output.

---

## What a Writer must do

A writer **receives an already-ready `TableIR` and serializes it to
disk**. It doesn't parse, it doesn't know where the data came from —
it receives `ir.data` (bytes) and that's it, regardless of whether the
source was CSV, `.c`, or any other future format.

```python
class Writer(Protocol):
    name: str          # identifier, used with --to
    extension: str      # e.g. ".hex" — determines the output file's name
    api_version: str

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        """Writes out_path from ir, returns the path written
        (usually just out_path itself)."""
        ...
```

If the writer can't produce valid output (e.g. data too large for the
format), it raises `WriterEmitError`:

```python
from payload.core.errors import WriterEmitError

raise WriterEmitError(self.name, "table too large for this format")
```

### Real example: `writers/hex_writer.py`

A minimal writer (`bin_writer.py`) just does `out_path.write_bytes(ir.data)`.
A more interesting example is `hex_writer.py`, which **transforms**
the bytes into Intel HEX format (used to flash firmware/data onto
microcontrollers):

```python
class HexWriter:
    name = "hex"
    extension = ".hex"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        if len(ir.data) > 0xFFFF:
            raise WriterEmitError(self.name, "table too large (>64KB)")

        lines = []
        for offset in range(0, len(ir.data), 16):
            chunk = ir.data[offset:offset + 16]
            lines.append(_data_record(offset, chunk))  # see the full file
        lines.append(_eof_record())

        out_path.write_text("\n".join(lines) + "\n")
        return out_path
```

Key point to notice: **this writer has no idea whether `ir` came from
a CSV, from `raw_text`, or from a future `.c` reader** — it only
receives bytes. It's exactly this decoupling that makes N readers × M
writers implementable with N+M plugins, not N×M.

---

## What a Doctor Check must do

A doctor check **verifies a precondition of the environment/project
and returns a verdict**, it doesn't take part in the reader→writer
build pipeline. It's the third plugin type (besides reader/writer) and
is what powers `pld doctor` / `GET /api/doctor` — meant for things
like "is the compiler in the PATH?", "is the config valid?", "are the
table names unique?" (the builtin checks in `payload/core/doctor.py`
are a good concrete reference).

Required interface (from `payload/core/plugin_base.py`):

```python
class DoctorCheck(Protocol):
    name: str          # unique identifier, shown next to the result
    api_version: str

    def run(self, config: dict) -> CheckResult:
        """Runs the check, ALWAYS returns a CheckResult — never an
        exception for a negative outcome, that's reserved for an
        unexpected error in the check itself (see below)."""
        ...
```

`CheckResult` (from `payload.core.plugin_base`):

```python
CheckResult(name: str, status: str, message: str, hint: str | None = None)
```

`status` is one of the three `CheckStatus` constants:

| Status | Meaning | Effect on `pld doctor` |
|---|---|---|
| `CheckStatus.OK` | everything fine | none |
| `CheckStatus.WARN` | non-blocking issue, the user should know about it | doesn't fail the command (exit code stays 0) |
| `CheckStatus.FAIL` | issue that will likely break a build | fails the command (exit code 1) |

`hint` is optional, shown only if the status isn't `OK` — use it to
say **how to fix it**, not just what went wrong (e.g. "install X"
instead of just "X not found").

### Real example: `ToolchainCheck`

```python
class ToolchainCheck:
    name = "toolchain"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        cmd = config.get("toolchain", {}).get("compiler")
        if not cmd:
            return CheckResult(self.name, CheckStatus.WARN, "'compiler' not configured")
        if not shutil.which(cmd):
            return CheckResult(
                self.name, CheckStatus.FAIL, f"'{cmd}' not found in PATH",
                hint=f"Install {cmd} or update 'compiler' in table-tool.toml",
            )
        return CheckResult(self.name, CheckStatus.OK, f"{cmd} found")
```

### What `config` contains

The same "resolved" dict (defaults + toolchain already merged
following the CLI > sidecar > global config > default priority) that
`parse()`/`emit()` would receive, plus one key doctor checks often use
that reader/writer usually don't: **`config["_project_root"]`**
(string) — the project folder, **always use it instead of the
process's cwd** to resolve relative paths. `pld serve` can run from a
folder different from the project it's serving: a check that reads/
writes relative to the cwd instead of `_project_root` pollutes the
wrong folder (see `DirWritableCheck`/`CacheIntegrityCheck` for the
correct idiom: `Path(config.get("_project_root", "."))`).

### A check must never make `pld doctor` blow up

`run()` runs alongside every other check, builtin and third-party: if
YOUR check raises a raw exception (not a `CheckResult` with `FAIL`
status), the core catches it and automatically converts it into a
`FAIL`-status `CheckResult` with the exception's message — it no
longer crashes the whole command/Doctor page, but **it's still
degraded behavior**: the message the user sees ("the check raised an
unexpected error...") is much less clear than a `FAIL` you wrote on
purpose. So: for an *expected* negative outcome (missing binary,
malformed file, etc.) always return an explicit
`CheckResult(..., CheckStatus.FAIL, "clear explanation", hint="...")`
— reserve exceptions for real bugs in your check.

This is also why the scaffold generated by
`pld plugin new-local <name> --kind doctor-check` (a file with
`raise NotImplementedError("TODO: implement the check")` instead of a
real `run()`) **no longer breaks `pld doctor`** if you open it before
finishing it: the check simply shows up as `FAIL` with that message,
instead of failing the whole command with a traceback. `pld doctor`
also includes a dedicated check (`local_plugin_stubs`, non-blocking)
that scans every local plugin in the project (reader/writer/doctor
check, not just doctor checks) and flags the ones whose `parse`/`emit`/
`run` is still an unimplemented scaffold, so you don't have to run
them to find out — and the same information shows up as a "not
implemented" badge on the "Plugin" page of the web UI, next to every
file in `local_plugins/`.

---

## How a plugin gets registered

A plugin is discoverable by the core through an `entry_point` declared
in the `pyproject.toml` of the package that contains it:

```toml
[project.entry-points."payload.readers"]
csv = "payload.readers.csv_reader:CsvReader"

[project.entry-points."payload.writers"]
hex = "payload.writers.hex_writer:HexWriter"
```

Available groups: `payload.readers`, `payload.writers`,
`payload.doctor_checks`. The name on the left (`csv`, `hex`) is what
you'll then use with `--from csv` / `--to hex`.

**Fastest way to get started**: `pld plugin new payload-reader-<name> --kind reader`
generates a complete scaffold (pip package, `pyproject.toml` with the
entry_point already correct, class stub, tests) — see the README.

---

## Handling endianness

**The problem**: `TableIR.data` is already-packed bytes. If a reader
reads `0x1234` as little-endian and writes `34 12`, a writer that just
does `out_path.write_bytes(ir.data)` has no way to know that those two
bytes represent *one* 16-bit value that should be rewritten as
`12 34` for a big-endian target — it's blind to field boundaries.

**The solution**: a reader that works with multi-byte values can (not
must) also expose the **structured values**, not just the final bytes:

```python
ir.byte_order = "little"          # order `data` is already packed in
ir.extra["fields"] = [
    {"offset": 0, "width": 2, "value": 0x1234},
    {"offset": 2, "width": 4, "value": 0xDEADBEEF},
]
```

A writer interested in endianness reads `config["defaults"]["byte_order"]`
(the target requested by the user/config) and, if it differs from
`ir.byte_order`, uses `payload.core.byteorder.repack(ir.extra["fields"], target_order)`
to rebuild the bytes in the right order — **without having to blindly
reinterpret raw bytes**, because it works on the original values, not
on bytes already packed by someone else.

```python
from payload.core.byteorder import repack

class MyWriter:
    def emit(self, ir, out_path, config):
        target = config.get("defaults", {}).get("byte_order", ir.byte_order)
        if target != ir.byte_order and ir.extra.get("fields"):
            out_path.write_bytes(repack(ir.extra["fields"], target))
        else:
            out_path.write_bytes(ir.data)  # no reinterpretation possible/needed
        return out_path
```

**So yes, you can have a reader that reads little-endian and a writer
that writes big-endian** — as long as the reader populates
`extra["fields"]`. If it doesn't (e.g. `raw_text.py`, which only works
with single bytes, where order doesn't matter), the writer can't do
anything smart: the correct behavior is to **warn and pass the bytes
through as-is**, never attempt a blind swap that could corrupt data.
`bin_writer.py` implements exactly this fallback — look at it as a
reference.

`config["defaults"]["byte_order"]` is configurable in `table-tool.toml`
or per-table in the sidecar (see [USAGE.md](USAGE.md)); a reader
should always pack `data` honoring that value (not a hardcoded order)
— `csv_reader.py` does exactly that.

---

## Linking reader and writer: defaults and compatibility

By default, **any reader works with any writer** — that's the whole
point of the N readers × M writers decoupling. But this creates two
practical problems:

1. You always have to specify `--to` explicitly, even when there's an
   obviously natural output for that input format.
2. If you pick (by mistake) a writer meant for a different format,
   nothing warns you — you get "valid" but silently wrong output.

Two optional attributes solve this:

**`Reader.default_writer`** — suggests the writer to use when neither
`--to` nor `defaults.writer` in config specify anything:

```python
class RawTextReader:
    name = "raw_text"
    default_writer = "bin"  # raw data format -> binary dump, the natural choice
```

Resolution order: explicit `--to` → `defaults.writer` in config (only
if someone actually set it — the project default is `None`, not an
arbitrary value) → `reader.default_writer` → a clear error
(`WriterNotSpecifiedError`) instead of a guessed fallback.

**`Writer.compatible_readers`** — if set, the writer rejects any
reader not listed, **before running `parse()`** (no wasted work on a
combination that would fail anyway):

```python
class MySpecificWriter:
    name = "my_format"
    compatible_readers = ["my_specific_reader"]  # rejects everything else
```

`None` (the default if you don't declare it) means "compatible with
any reader" — correct for writers like `bin`/`hex` that serialize
bytes without interpreting them, so they have no reason to be
restrictive. Only declare it if your writer **requires** specific
semantics from the reader (e.g. it always expects `extra["fields"]`
populated in a particular way).

---

## Passing extra information to a plugin

`config` (the dict `parse()`/`emit()` receive) only contains
`defaults`/`toolchain` by default — if your plugin needs something the
core doesn't know about (a CSV delimiter, a base address, a
format-specific flag), you have **two channels**, for two different
purposes:

**1. `[plugin.<name>]` — persistent, in `table-tool.toml`/sidecar**

Not validated by the core (unlike `defaults`/`toolchain`): it's plugin
territory, the core has no way to know which keys are legitimate for a
third-party plugin.

```toml
# table-tool.toml, or <table>.config.toml for the sidecar
[plugin.csv]
delimiter = ";"
```

```python
def parse(self, path: Path, config: dict) -> TableIR:
    delimiter = config.get("plugin", {}).get("csv", {}).get("delimiter", ",")
    ...
```

**2. `--opt key=value` — one-off, only for this invocation**

Doesn't touch any file, doesn't persist. Useful for a quick test or a
script that wants a different override every time:

```bash
pld build sensors/temp.csv --to bin --opt delimiter=";"
```

```python
def parse(self, path: Path, config: dict) -> TableIR:
    override = config.get("cli_opts", {}).get("delimiter")
    ...
```

**`--opt` wins over `[plugin.*]`**, which wins over the plugin's own
default — same priority principle already used elsewhere (CLI >
config > default). Both channels go into the cache key: changing a
`--opt` or a value in `[plugin.*]` correctly invalidates the cache,
you don't need `--force`.

---

## Local plugins, without `pip install`

A "real" plugin (meant to be reused across several projects,
distributed, versioned) should be packaged with `pld plugin new` +
`pip install -e .` — that's what gives you `entry_points`, independent
versioning, installability via an internal pip index.

For a quick experiment or a format specific to **a single project**,
that ceremony can be overkill. `payload` also discovers plugins as
**single `.py` files**, with no installation at all:

**Where to put them** — two ways, can be combined:
1. A `local_plugins/` folder next to `table-tool.toml` — discovered
   automatically.
2. The `PAYLOAD_PLUGIN_PATH` environment variable (list of folders
   separated by `:` on Unix, `;` on Windows) — useful for sharing
   plugins across several projects without publishing them as a
   package.

**Convention in the file**: expose the class as a module-level
variable, `READER`/`WRITER`/`DOCTOR_CHECK` for a single plugin, or
`READERS`/`WRITERS`/`DOCTOR_CHECKS` (lists) for several plugins in the
same file:

```python
# local_plugins/my_writer.py
class UpperWriter:
    """Converts the data to uppercase before writing it (example)."""
    name = "upper"
    extension = ".upper"
    api_version = "1.0"

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data.upper())
        return out_path

WRITER = UpperWriter  # <- this line is what makes it discoverable
```

From that point on `pld build table.raw --to upper` works, with no
`pip install`. Files starting with `_` are ignored (useful for helper
modules shared between several local plugins that aren't themselves a
plugin).

### If the plugin needs third-party libraries

A local plugin can declare its own dependencies with a module-level
`REQUIRES`:

```python
# local_plugins/my_writer.py
REQUIRES = ["numpy>=1.20", "pyserial"]

class MyWriter:
    ...
```

Checked **before** attempting to load the module (a static read of the
source via `ast`, without executing it) — so even though the module
would fail with an unclear `ModuleNotFoundError` because `numpy` isn't
installed, the error you see says exactly which dependency is missing,
instead of a generic traceback:

```bash
pld plugin install-deps local_plugins/my_writer.py
```

installs, with `pip`, into the current environment, everything
`REQUIRES` declares that isn't already present. `pld doctor` also
includes a check (`local_plugin_deps`, non-blocking) that scans every
local plugin in the project and flags the ones with missing
dependencies, without you having to check them one by one.

**Honest limitation**: the check only verifies "is the package
importable, yes or no" — it's not real dependency resolution (it
doesn't check that the installed version satisfies `>=1.20`, for
example). For that you still need a real pip-managed environment with
a `requirements.txt`/version pinning, if your project genuinely needs
it — `REQUIRES` is meant for "completely missing" much more than for
"wrong version".

**Limits to keep in mind**: no independent versioning for the plugin
itself, no easy distribution to other teams (a real pip package is
still better for that). For everything else — errors
(`ReaderParseError`/`WriterEmitError`), `default_writer`/`compatible_readers`,
conformance (`pld plugin validate`) — it works exactly like a plugin
installed via pip.

---

## How tables are organized across directories

**No structure is imposed.** A table is a source file; you can have as
many as you want in the same folder, and the folder hierarchy is free
— `pld build-all` recursively discovers every source under the root,
at any depth.

```
project/
├── table-tool.toml
├── sensors/
│   ├── temp_table.raw
│   ├── temp_table.config.toml   # sidecar, same name, same folder
│   └── pressure_table.csv
└── actuators/
    └── output_table.raw
```

**The one real constraint**: the **table name** (the filename without
its extension) must be **unique across the whole project**, not just
within the same folder. The name is the identity used everywhere —
output files in `build/`, snapshots and the golden reference in
`.payload_history/` — all indexed by name, not by full path. Two files
with the same stem in different folders (`sensors/temp.raw` and
`actuators/temp.raw`) silently collide on these fronts. `pld build-all`
and `pld doctor` detect this collision and flag it with
`DuplicateTableNameError` instead of letting you discover an overwrite
by surprise.

---

## Checklist before considering a plugin ready

- [ ] `name`, `api_version` present (for a reader also `extensions`, for a writer `extension`)
- [ ] `api_version = PLUGIN_API_VERSION` imported from `payload.core.ir` (not a hardcoded string)
- [ ] **Docstring on the CLASS** (not just the module) explaining the format with a concrete example — it's what `pld plugin info <name>` shows to anyone who installs your plugin without reading the code
- [ ] Errors raised as `ReaderParseError`/`WriterEmitError`, never a generic `Exception`
- [ ] `sniff()` implemented only if disambiguation is really needed (otherwise `return False` is fine)
- [ ] No dependency on the format of the other side of the pipeline (where it came from / where it's going)
- [ ] Entry point declared in the right group (`payload.readers` / `payload.writers`)
- [ ] `pld plugins` shows the plugin after `pip install -e .`

---

## Validating that the plugin really honors the contract

Just "writing tests" on your own doesn't guarantee the plugin is
correct — a test might not check anything meaningful. `payload`
instead provides a **conformance suite** (`payload.testing`) that
verifies specific behaviors of the Reader/Writer contract: correct
return type, errors raised properly, required attributes present.

In your plugin, in a pytest test:

```python
from payload.testing import assert_reader_conforms

def test_my_reader_conforms(tmp_path):
    sample = tmp_path / "sample.myext"
    sample.write_text("...")  # valid content for your format
    assert_reader_conforms(MyReader(), sample)
```

```python
from payload.testing import assert_writer_conforms
from payload.core.ir import TableIR

def test_my_writer_conforms(tmp_path):
    sample_ir = TableIR(
        name="sample", data=b"\x00\x01\x02",
        source_path=Path("sample"), source_format="testing",
    )
    assert_writer_conforms(MyWriter(), sample_ir, tmp_path)
```

`pld plugin new` already generates these tests as commented-out stubs
— just uncomment them and adapt the sample.

**Even without pytest**, you can validate an already-installed plugin
at runtime:

```bash
pld plugin validate <name> --sample path/to/sample/file.ext
```

This runs the same suite without needing the package's test suite —
useful for quickly checking a third-party plugin before trusting it,
or in CI right after installation.

**Why it's not a blocking requirement at load time**: when a plugin is
installed via `pip`, its development tests aren't distributed together
with the package — the core has no way to know at runtime whether they
exist, let alone whether they pass. `pld plugin validate` is meant to
be run explicitly (by hand or in CI), not as an automatic gate at load
time — an automatic gate would silently break the tool for anyone
installing a third-party plugin written before this suite existed.
