# HOW TO — a guided tour of payload

A step-by-step walkthrough of every operation the tool supports, using
the sample files in `examples/howto/` and the plugins in
`examples/plugins/`. Read [USAGE.md](USAGE.md) for the full command
reference; this guide is the "follow along" version.

> Every command here runs in the project directory (the CLI resolves
> the project root from the current directory). All commands also
> accept `--root <dir>` if you prefer to stay where you are.

---

## 0. Install and create a project

```bash
pip install payload          # or: pip install -e . from the repo
pld init device_fw           # scaffolds table-tool.toml + build/ + tmp/ + plugins/
cd device_fw
```

Install the example plugins (payload ships no reader/writer of its
own — this is exactly what a real user does):

```bash
pld plugin install ../examples/plugins/raw_text.py
pld plugin install ../examples/plugins/csv_reader.py
pld plugin install ../examples/plugins/bin_writer.py
pld plugin install ../examples/plugins/hex_writer.py
pld plugins                 # raw_text (reader), csv (reader), bin (writer), hex (writer)
```

Copy the sample files in:

```bash
cp ../examples/howto/status.raw .
cp ../examples/howto/temperature.csv .
cp ../examples/howto/row_A.txt ../examples/howto/row_B.txt .
```

---

## 1. Your first table: import → build → commit → golden

```bash
pld import status.raw                    # copies status.raw in as table 'status'
pld ls                                   # what do I have? (status, never built)
pld build status.raw                     # raw_text -> bin -> build/status.bin
pld ls                                   # status, built
pld commit -m "first version"            # snapshot of source + output
pld golden set status                    # this snapshot becomes the golden reference
```

Change the source and rebuild — the tool now knows the golden is
stale, and a rebuild makes it a mismatch:

```bash
sed -i '' 's/0xFF/0xFE/' status.raw      # tweak a byte
pld build status.raw
pld golden check status                  # fails: source changed since the golden (stale)
pld commit -m "flags changed"            # new snapshot
pld golden set status                    # new golden
pld status                               # clean again
```

---

## 2. Structured data and endianness

`temperature.csv` has multi-byte values; the `csv` reader honors
`byte_order`. Set per-table overrides (the sidecar — the CLI twin of
the webapp's per-row **Settings** modal):

```bash
pld config set temperature --reader csv --byte-order little
pld build temperature.csv                # little-endian: 0x1234 -> 34 12
pld config set temperature --byte-order big
pld build --force temperature.csv        # 0x1234 -> 12 34 (big-endian)
pld config show temperature              # resolved config + where each value comes from
```

The byte_order change is itself a committable change, even when the
bytes come out identical (a reader without multi-byte fields):

```bash
pld commit -m "big endian" --only temperature
```

---

## 3. A batch table (several files, one logical table)

```bash
pld import row_A.txt row_B.txt --new-batch rows
pld batch                                # rows: row_A.txt, row_B.txt
pld batch rows --reader raw_text         # override the reader for this batch
pld batch rows --stage reader:raw_text --stage writer:bin   # explicit pipeline
pld batch rows                           # show members + overrides + stages
pld build rows                           # builds the batch by name
pld commit -m "batch v1" --only rows
```

`--stage ""` clears the pipeline; `--reader ""` clears an override.

---

## 4. Organizing: clusters, tags, notes and properties

```bash
pld cluster new sensors --writer hex     # a shared-config group
pld cluster assign temperature sensors
pld cluster show sensors

pld tag temperature --add prod --add fw
pld tags                                 # every tag in use, with counts

pld meta temperature --note "calibrated at boot"
pld meta temperature --prop address=0x8000 --prop fw=2.1
pld meta temperature                     # notes + properties
```

Properties reach the writer at build time via
`config['table_meta']['properties']` — table-owned values (address,
version, …) flow into the binary without being hardcoded in the
source.

---

## 5. Pipelines

```bash
pld pipeline show temperature            # implicit: reader csv -> writer bin
pld pipeline show rows                   # explicit stages from the batch entry
```

An `exec` stage runs an external command on a file between stages —
reuse scripts you already have:

```bash
pld batch rows --stage reader:raw_text --stage writer:bin \
   --stage "exec:objcopy -I binary -O ihex"
```

The webapp's pipeline editor (see §9) is the graphical way to build
the same pipelines: free canvas, drag ports to connect, auto layout,
Save validates the whole sequence.

---

## 6. History, safety, and cleanup

```bash
pld log temperature                      # snapshot history
pld diff temperature --snapshot 1        # current source vs that snapshot
pld restore temperature 1                # bring source + output back to snapshot 1
pld rm rows --force                      # delete source(s) + output (history stays)
pld clean --target cache --target build   # empty the build cache / outputs
pld clone temperature temperature_v2     # new table with fresh history
pld rename-table temperature_v2 temp2    # end-to-end rename
```

---

## 7. Analysis and search

```bash
pld analyze build/status.bin             # entropy, printable ratio, magic candidates
pld grep "0xFF"                          # find bytes/text across the project
pld compare build/status.bin build/status_old.bin   # common prefix/suffix diff
pld activity                             # project timeline (builds, commits, …)
```

---

## 8. Sharing

```bash
pld report                               # one row per table: sizes, byte_order, golden
pld report --html report.html            # printable HTML (open + 'Save as PDF')
pld export payload.zip --include-history  # portable archive: sources, config, history
```

---

## 9. The webapp

```bash
pld serve                                # http://127.0.0.1:8000
```

- **Dashboard** (`/`): the 5-column table (name, status, golden, size,
  actions). Filter by name/tag/cluster/note/property; **Settings**
  (gear) per row opens the overrides modal (sidecar for single tables,
  members + overrides for batches); **Build all** modal; **Report**;
  import by dragging files (one file → a table, several → offer a batch).
- **Table page** (`/table/<name>`): build form with `--preview-diff`
  (side-by-side byte diff vs golden before committing), source editor,
  the **pipeline editor** (Edit graph), tags/cluster + notes/properties,
  history with commit/restore/golden, and Inspect (diff vs snapshot /
  golden, analyze output). Batch tables show their members and inline
  overrides instead of the source editor.
- **Files** (`/files`): the whole project in the browser — tree,
  multi-select, drag & drop, right-click menu, text editor, hex viewer
  with Strings, compare/grep/analyze as buttons.
- **Activity** (`/log`): the project timeline, paged (Load more).
- **Settings** (gear in the sidebar): the global `table-tool.toml`
  form with origin pills.

The web talks to the same core: a table built from the web is the
same table built from the CLI.

---

## 10. Doctor and conventions

```bash
pld doctor                               # toolchain, plugins, config, directories
```

Conventions worth knowing:

- a table is a source file; its name is the file's stem (a batch
  table is a `[[batch_table]]` entry with N source files);
- one source file can't be both a single table and a batch member;
- golden = the snapshot the project considers "correct" — mismatch and
  stale are reported everywhere (dashboard, `pld status`, build);
- deleting a table keeps its history: restore it from the dashboard
  or `pld restore`;
- a file can carry a sidecar (`<name>.config.toml`) with per-table
  overrides; batches carry theirs inline in `table-tool.toml`.
