# Batch tables — a table built from several source files

This document describes `[[batch_table]]`: the way to declare a
logical table made up of **several source files of the same format**
(e.g. `ROW1.txt, ROW2.txt, ..., ROWn.txt`), read together by a single
reader into one `TableIR`, which then continues through the normal
writer/fan-out pipeline **with no difference at all** compared to a
single-file table.

---

## The concept in one line

```
[file1, file2, ..., fileN] → Reader.parse_many() → TableIR → [stage] → ... → output
```

Past this first step, a batch table is indistinguishable from a normal
one: same execution engine (`core/pipeline.py`), same multi-writer
fan-out, same history/golden system, same incremental cache — only the
"source" identity is a set of files instead of just one.

---

## Why explicit config, not a naming convention

A batch table is **always declared explicitly** in `table-tool.toml`,
never through a filename convention or folder structure — consistent
with the approach already used for `[pipeline.stages]` and sidecars:
no implicit heuristic that could accidentally group files together.

```toml
[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
```

`name` is the table's identity everywhere in the tool (build, history,
golden, cache) — exactly like the filename stem is for a normal table.
It must be **unique across the whole project**, the same way table
names derived from files are: it collides with a real stem or with
another `[[batch_table]]` → `DuplicateTableNameError`.

**A file declared as a source of a `[[batch_table]]` no longer shows
up as a standalone table** in normal discovery, even if its extension
is recognized by a reader — otherwise `ROW1.txt` would be discovered
twice: once as part of the batch and once as the standalone table
`ROW1`, with duplicated build/output.

---

## `[[batch_table]]` fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | table name, unique across the project |
| `sources` | yes | list of paths/patterns (see below for ordering) |
| `reader` | no | reader override, like `defaults.reader` |
| `writer` | no | writer override, like `defaults.writer` |
| `byte_order` | no | override of `defaults.byte_order` |
| `stages` | no | explicit pipeline, same shape as `[pipeline.stages]` (see [PIPELINE.md](PIPELINE.md)) |

These overrides live **inline in the `[[batch_table]]` block**, not in
a sidecar: a batch table has no single `source_path` to resolve a
`<name>.config.toml` from. If unspecified, the global
`[defaults]`/`[pipeline]` defaults apply exactly as they would for a
normal table.

```toml
[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
reader = "raw_text"
writer = "hex"
byte_order = "big"
```

`[[batch_table]]` is read **only from the global `table-tool.toml`** —
an occurrence in a sidecar (which wouldn't make sense anyway, since
sidecars are per-single-file-table) is simply ignored.

---

## File order matters

Files are concatenated in the order they appear in
`sources` — important for line-by-line formats like `raw_text`, where
the order of the data in the final file depends on the order of the
sources. `sources` accepts **both glob patterns and literal paths,
even mixed in the same list**:

- **A literal entry** (no glob metacharacter: `*`, `?`, `[`) keeps
  exactly the position given in the list — full control for the user.
- **A glob entry** is expanded and sorted with a "natural sort"
  comparison (numeric on the digit runs in the filename), **not**
  pure lexicographic — so `ROW2.txt` comes before `ROW10.txt` even
  with `sources = ["ROW*.txt"]`, while a lexicographic order would put
  `ROW10.txt` before `ROW2.txt`.

```toml
# Automatic expansion, natural order (ROW1, ROW2, ..., ROW10, ...)
sources = ["ROW*.txt"]

# Explicit order control (useful if the "right" order isn't the
# numeric order of the filenames)
sources = ["intro.txt", "ROW3.txt", "ROW1.txt", "coda.txt"]
```

Every resolved file must have a **filename unique within the batch**
(regardless of folder) — two different sources with the same name,
e.g. `sensors/ROW1.txt` and `actuators/ROW1.txt` in the same batch,
are a configuration error (`BatchTableError`): the history
(`source_blobs`, see below) is indexed by filename, a collision would
silently lose a file.

---

## The Reader contract: `parse_many`

A reader must implement `parse_many(self, paths: list[Path], config: dict) -> TableIR`
(in addition to `parse()`, which stays mandatory and unchanged) to be
usable in a batch table — see
[PLUGINS.md](PLUGINS.md#optional-extension-parse_many-batch-tables)
for the full contract. A reader that doesn't implement it makes the
build fail with `ReaderBatchUnsupportedError`, a clear error instead of
a fallback that blindly concatenates bytes (wrong for formats that
aren't line-by-line). `raw_text` already implements `parse_many` — it's
the reference reader for the `ROW1.txt..ROWn.txt` example.

---

## Cache, history, golden: what changes

Conceptually **nothing** — only the source identity becomes plural:

- **Cache**: the freshness key incorporates the hash of **all** source
  files (name, length, and content of each, in order), not just one —
  changing even a single member file invalidates the whole table's
  cache.
- **History**: a snapshot records a blob for **every** source file
  (`{filename: hash}`, the same schema already used for outputs)
  instead of just one. `pld log`/`pld diff`/`pld restore` work the
  same way, reporting which member file changed.
- **Golden**: `stale` triggers if **any** of the source files changed
  after the golden snapshot — same logic as before, just applied to a
  set instead of a single file.
- **A file added or removed from the batch between two commits** is
  detected as "changed"/`dirty` even if the content of the other files
  didn't change — the source set's keys changed, not just the values.

---

## Using it from the CLI and web

```bash
pld build rows                 # the [[batch_table]] name, not a path
pld build-all                  # automatically includes batch tables
pld status                     # shows "rows" with a "(batch, N files)" marker
pld commit -m "..."            # also commits changed batch tables
pld log rows
pld diff rows                  # differences for each changed member file
pld restore rows <N>           # works even if "rows" was fully removed — see 'pld rm' below
pld golden set rows
pld import a.txt b.txt --new-batch rows   # creates the "rows" [[batch_table]] from two files
pld import c.txt --batch rows             # adds a third member
pld rm rows --member c.txt --force        # removes a member (removing the last one removes the whole entry)
pld rm rows --force                       # deletes all members + the [[batch_table]]
```

On the web side, `pld serve` exposes the same behavior through the
existing routes (dashboard, table page, history, golden), plus
`/api/table/import` (creation, also via drag&drop on the Dashboard) and
`/api/table/delete` — the only thing that stays config-file-only is
**editing** an already-existing `[[batch_table]]` (inline
reader/writer/byte_order/stages, or reordering `sources`): no visual
editor for that at this stage, you edit `table-tool.toml` by hand,
exactly like `[pipeline.stages]` started out config-file-only before
getting a visual builder in a later phase.

---

## Explicit limits at this stage

- **`pld view` doesn't support batch tables** — there's no obvious
  "which file do I show" mapping for a command meant to inspect a
  single file.
- **No source editor for batch tables** in the web UI (route
  `/api/source/{table}`): a single-file editor doesn't apply to N
  files, the route explicitly rejects it with a clear error instead of
  showing/editing just one of the members in a misleading way.
- **No resume from an intermediate checkpoint** for a batch build
  interrupted midway through a multi-stage pipeline — it starts over
  from scratch on the next attempt (still correct behavior, just not
  as optimized as for a single-file table).
- **A new member file matched by a glob (not a literal path) isn't
  picked up while `pld watch` is already running** — `sources =
  ["ROW*.txt"]` is expanded once at watch startup; a file created
  afterward that would match the glob is treated as its own
  standalone table until watch is restarted. A literal path added to
  `sources` by hand always requires a restart anyway (it's a config
  change), so this only affects the glob case.

### Closed in this version

- **`pld watch` rebuilds a batch table when a member file changes** —
  live-reload now covers the whole batch (all its member files are
  re-read and the table rebuilt as one unit), not just single-file
  tables. If several members are saved within the debounce window,
  the batch rebuilds once per file that settles — redundant but
  harmless, the cache makes the extra runs cheap.
- **`pld restore` recreates an entirely deleted batch table**
  (`pld rm rows` without `--member`, which also removes the
  `[[batch_table]]` from config): the source files are written back
  from history AND the `[[batch_table]]` entry is re-added to
  `table-tool.toml`, using the reader/writer recorded at commit time.
  An explicit multi-stage `stages` pipeline, if there was one, is
  **not** reconstructed automatically (there's no reliable way to turn
  the snapshot's human-readable pipeline description back into
  `stages` entries) — `pld restore` prints what the recorded pipeline
  was so it can be re-added by hand.

---

## Full example

```
project/
├── table-tool.toml
├── ROW1.txt
├── ROW2.txt
└── ROW3.txt
```

```toml
# table-tool.toml
[defaults]
writer = "bin"

[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
```

```bash
pld build rows            # reads ROW1.txt, ROW2.txt, ROW3.txt in natural order
                           # -> build/rows.bin
pld commit -m "first version of rows"
pld golden set rows
```
