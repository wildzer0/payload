# The pipeline — how it works

This document describes the model `payload` uses to run **every**
build, from the simplest (a reader + a writer) to the most complex
(several stages with external transformations in between).

Note on `exec` and Windows: the command inside an `exec` stage runs
through the host's shell, so its syntax follows `cmd.exe`/PowerShell
rules on Windows and POSIX shell rules on Linux/macOS — the same
`exec` command isn't automatically portable between the two.

---

## The concept in one line

```
source → [stage] → [stage] → [stage] → ... → final output
```

**There's no longer a "simple" build as a special case.** Even
`pld build table.raw --to bin` (a reader + a writer) is internally a
2-stage pipeline — you just don't have to write it explicitly, because
the tool builds it on its own from `--from`/`--to`. The exact same
execution engine handles both this implicit case and a 6-stage
pipeline written by hand in a sidecar. One code path, zero special
cases to maintain in parallel.

---

## The three stage types

| Type | Signature | What it does |
|---|---|---|
| `reader` | file → in-memory data | Reads a file, produces `TableIR` (exactly like readers today) |
| `writer` | in-memory data → file | Writes `TableIR` to disk (exactly like writers today) |
| `exec` | file → file | Runs an external command (shell/host tool) on a file, produces another file |

`exec` is the new piece: it never touches `TableIR`, it only works on
files — it's how an external tool (a signer, a compressor, a
proprietary host tool) enters the pipeline without `payload` having to
know anything about its internal format.

---

## The alternation rules

Not every sequence of stages is valid. The rules, designed to be
checkable **before** launching any build (not midway through one):

1. **The first stage must be a `reader`** — the pipeline always starts
   from the table's source file.
2. **A `reader` must be immediately followed by a `writer`** — a
   reader produces in-memory data (`TableIR`); the only thing that can
   consume it is a writer. There's no "reader after reader": there's
   nothing a second reader could read from a `TableIR` still in
   memory, it has to be written to disk first.
3. **After a `writer`, you can put**: a `reader` (to re-read that file
   and keep working with it as data), an `exec` (to transform it while
   staying at the file level), or **nothing** — that `writer` produces
   the final output.
4. **After an `exec`, you can put**: a `reader`, another `exec`, or
   **nothing** — that `exec` produces the final output.
5. **The pipeline must have at least 2 stages**: a `reader` and a
   `writer` — the bare minimum, which is exactly today's behavior.
6. **Fan-out**: a `reader` can be followed by **several consecutive
   `writer`s**, not just one — all fed from the same IR, parsed only
   once (see the dedicated section below). The only constraint: a
   group of 2+ consecutive writers must be the **last** thing in the
   pipeline — no `reader`/`exec` can come after a fan-out. A group of
   a single writer has no such restriction (the usual behavior).

In short, as a state machine:

```
[start] → reader → writer → { end | reader | exec | another writer* }
                                            ↑___________|
                              exec → { end | reader | exec }

  * "another writer" (fan-out) is only allowed if it leads all the way
    to the end of the pipeline — no reader/exec after a group of 2+ writers.
```

A config that violates these rules (e.g. two `reader`s in a row, or
one that ends with a `reader`, or an `exec` after a fan-out) is
rejected **at validation time**, before touching any file — the same
principle already used for validating the existing config
(`InvalidConfigError`).

---

## Fan-out: several writers from the same parse

Common use case: you want to produce `.bin` **and** `.hex` **and** a
`.h` header from the same table, without re-reading/re-parsing the
source three times (with an expensive reader — e.g. `c_source`, which
compiles with gcc — the difference is real, not just stylistic).

```toml
[pipeline]
stages = [
    { type = "reader", name = "csv" },
    { type = "writer", name = "bin" },
    { type = "writer", name = "hex" },
    { type = "writer", name = "header" },
]
```

The reader runs **exactly once**; the resulting IR is passed,
unchanged, to each of the three writers, which each write their own
file (`table.bin`, `table.hex`, `table.h`) into the output directory.
Reader/writer compatibility is checked for **every** writer in the
group, not just the first — a writer whose `compatible_readers`
excludes the reader used blocks the build before any file gets
written, just like a linear pipeline.

`build()` therefore always returns a **list** of paths (one element in
the common case, one per writer with a fan-out) — see
`core/pipeline.py`, `final_output_paths()`.

**Partial failure**: if a fan-out has **more than one** terminal
writer, each is treated independently — a writer that fails at
runtime (e.g. a toolchain error) does NOT block the others, which are
still attempted and write their own file normally. The build still
fails at the end (it's not a silent success), but with
`FanOutWriteError`, which explicitly lists which writers succeeded
(with the paths written) and which failed (with the reason) — without
this, a 3-writer fan-out with only 1 failure would hide the fact that
the other 2 really are on disk. A fan-out with a single terminal
writer (i.e.: not actually a fan-out) doesn't get this treatment: it
just fails, there's nothing "partial" to report. If you then commit a
state born from a partial failure, the snapshot flags it
(`missing_outputs`, see `pld commit`/`pld log` in USAGE.md) — it
doesn't go unnoticed further down the line either.

**What's NOT supported**: a fan-out with **per-branch continuation** —
e.g. `writer bin -> exec sign-v1` and, in parallel, `writer hex -> exec
sign-v2`, each with its own independent later stages. Every writer in
a fan-out is always a **terminal** stage: if you need a different
transformation for each output, use separate pipelines (one per
output, with the same reader repeated).

---

## Config syntax

### Explicit pipeline, in `table-tool.toml` or in a sidecar

```toml
[pipeline]
stages = [
    { type = "reader", name = "c_source" },
    { type = "writer", name = "bin" },
    { type = "exec", command = "sign_tool.exe {input} {output}" },
    { type = "reader", name = "raw_text" },
    { type = "writer", name = "obj" },
]
```

If a table has a `[pipeline]` in its own sidecar, that one wins —
`--from`/`--to` from the CLI are ignored for that build (with a
warning, not a silent error: an explicit pipeline already declares
everything it needs, mixing the two ways of specifying it would be
ambiguous).

### Implicit form (shorthand) — what you already use today

```bash
pld build table.raw --to bin
```

Internally this becomes:
```python
PipelineSpec(stages=[
    ReaderStage(name="raw_text"),   # resolved by extension, as today
    WriterStage(name="bin"),         # from --to, or the reader's default_writer
])
```

No new syntax to learn for the common case — the explicit pipeline is
there for when you really need it, not a requirement for every table.

---

## Intermediate files

Every stage that doesn't produce the final output still writes a real
file to disk (never just in memory between a writer and an exec, for
example) — they're inspectable, and any failed command says exactly
which file it was working on.

Where they live: in a `tmp/` folder next to the source (the same
convention already used by `c_source`/`obj`), **cleaned up
automatically at the end of the build**. With `--keep-intermediate`
(on `pld build`/`pld build-all`), the folder is kept for manual
inspection — useful when debugging an `exec` that produces an
unexpected result and you want to see the exact input it received.

---

## `exec` stages — the details

```toml
{ type = "exec", command = "sign_tool.exe {input} {output}", on_error = "fail" }
```

**Available placeholders** in the command: `{input}` (path of the
current file), `{output}` (the path the tool is expected to produce as
a result — generated automatically in `tmp/`), `{table_name}` (the
table's name).

**After running**, the tool checks that `{output}` really exists on
disk — if the command returns 0 but didn't produce the expected file,
that's a clear error (`ToolchainExecutionError`), not a surprise crash
in the next stage that expects to find it.

**`on_error`**: `"fail"` (default, stops the build) or `"warn"` (logs
and continues with the last valid file — meant for non-essential
stages like a notification, where a failure shouldn't block the main
output).

---

## Security — don't underestimate this

An `exec` stage runs arbitrary code read from a config file. If that
`table-tool.toml`/sidecar comes from someone else (a colleague, a
shared repo, an external tool that generates config), you're running
arbitrary commands without necessarily realizing it.

**`pld doctor` always visibly flags how many `exec` stages are
configured in the project** — the `pipeline_exec` check scans the
global config and every sidecar: `"3 'exec' stages configured across 2
files"`, with the list of files involved in the hint. Informational
(`WARN`, not `FAIL`), but made impossible to miss — run `pld doctor`
before launching a build on a config you don't 100% trust.

---

## Cache

Two levels, both active automatically (no config needed to enable
them):

**Whole-pipeline cache** — if the source, the entire list of stages,
and the config are identical to a previous run, the final output is
reused without running anything. Same logic as always, now over the
signature of every stage instead of just reader+writer.

**Per-stage cache** — for every `writer`/`exec` stage that is *not*
the last one, its output is persisted (outside `tmp/`, which gets
cleaned on every build) together with a key computed on the pipeline
**prefix** up to that point. On the next build, if a prefix matches
one already cached, execution **resumes from there** — the earlier
stages (including a possibly expensive `.c` compilation) don't get
re-run, even if the *later* stages have changed.

```
reader(c_source) → writer(bin) → exec(sign v1)     [first run: everything runs]
reader(c_source) → writer(bin) → exec(sign v2)     [second run: reader+writer
                                                      SKIPPED, resumes from exec]
```

`--force` bypasses the stage checkpoints too, not just the final
cache. `pld pipeline show <table>` shows which stages currently have a
valid checkpoint.

---

## `--dry-run`

Shows every stage that would run, in order, **without running the
`exec`s** — an external command can have real side effects (upload,
talking to hardware), a dry run must never risk triggering them by
accident. For `reader`/`writer` it only shows what would be written,
as it already does today.

---

## Known limits

- **Fan-out with per-branch continuation**: not supported, see the
  "Fan-out" section above — every writer in a fan-out is always a
  terminal stage.
- **Conditional stages** (e.g. "run this stage only if some condition
  is true"): there's no syntax for this in `[pipeline] stages`. If you
  need it, handle it upstream with different configs (global vs.
  sidecar) for the tables that need it.
