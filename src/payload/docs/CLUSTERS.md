# Clusters & tags — organizing and configuring many tables at once

This document describes two related, but independent, mechanisms for
a project with more than a handful of tables: **clusters** (at most
one per table, carries config overrides) and **tags** (any number per
table, purely organizational, used to search/filter).

---

## The concept in one line

```
global [defaults] → the table's cluster (if any) → sidecar / batch-inline overrides → CLI flags
```

A cluster is a named bundle of `defaults`/`plugin` overrides, declared
once in `table-tool.toml`, that any number of tables can opt into —
**at most one cluster per table**. It's the fourth tier in config
resolution, sitting between the project-wide `[defaults]` and a
table's own sidecar (or, for a batch table, its inline overrides in
`[[batch_table]]` — see [BATCH.md](BATCH.md)).

Tags are unrelated to config: a table can have any number of free-form
tags, used only to search/filter in the dashboard and `pld report`/
`pld status`. A tag never affects a build.

---

## Why this exists

A project with 5 tables doesn't need this. A project with 200 — say,
50 sensor boards that all build with the same writer and a custom
`output_dir`, mixed with 150 unrelated tables — either repeats the
same sidecar 50 times, or leans on the global `[defaults]` and loses
per-group control. A cluster lets you declare that shared configuration
**once** and opt tables into it; a tag lets you find "all the sensor
board tables" in the dashboard without needing a cluster for it (a
table can be tagged without being clustered, and vice versa).

---

## Why `defaults` + `plugin`, not `pipeline`

A cluster can override the same two sections a sidecar can: `defaults`
(writer/reader/output_dir/cache_dir/byte_order) and `plugin.<name>`
(opaque, plugin-owned config — see
[PLUGINS.md](PLUGINS.md#passing-extra-information-to-a-plugin)).

It deliberately **cannot** override `[pipeline]` stages. A cluster
silently changing a multi-stage pipeline for every table that opts
into it is a much bigger footgun than a `writer`/`output_dir` tweak —
if a group of tables needs a shared explicit pipeline, that's still a
per-table (or per-sidecar) decision today. This may be revisited later
if it turns out to matter in practice.

---

## `[[cluster]]` and `[[table_meta]]`

Two separate top-level sections in `table-tool.toml`:

```toml
[[cluster]]
name = "sensor-boards"

  [cluster.defaults]
  writer = "c_header"
  output_dir = "build/sensors"

  [cluster.plugin.c_source]
  compiler = "arm-none-eabi-gcc"

[[table_meta]]
name = "temp_sensor"
cluster = "sensor-boards"
tags = ["sensor", "beta"]

[[table_meta]]
name = "humidity_sensor"
tags = ["sensor"]          # tagged, but not in any cluster
```

The nested-header syntax above (`[cluster.defaults]` right after
`[[cluster]]`) and an equivalent inline-table form parse identically —
pick whichever reads better for your file:

```toml
[[cluster]]
name = "sensor-boards"
defaults = { writer = "c_header", output_dir = "build/sensors" }
plugin = { c_source = { compiler = "arm-none-eabi-gcc" } }
```

`[[cluster]]` declares the named override bundle. `[[table_meta]]` is
per-table metadata — cluster assignment (`cluster`, optional, at most
one) and tags (`tags`, optional, any number) — keyed by table `name`.
It's deliberately **separate** from both `[[batch_table]]` (which
*declares* a table's existence) and a sidecar (which overrides a
single table's build config): `[[table_meta]]` doesn't do either of
those, it only points at a `[[cluster]]` and/or lists tags, and it
applies the same way to single-file and batch tables alike — neither
of which has another natural place to hold this.

A table not listed in `[[table_meta]]` simply has no cluster and no
tags — nothing to declare, nothing written.

---

## Precedence, worked example

```toml
[defaults]
writer = "bin"

[[cluster]]
name = "sensors"
[cluster.defaults]
writer = "hex"
output_dir = "build/sensors"

[[table_meta]]
name = "temp_sensor"
cluster = "sensors"
```

`sensors/temp_table.config.toml` (sidecar):
```toml
[defaults]
writer = "c_header"
```

Resolving `temp_sensor`: `writer` is set at all three levels — global
(`bin`), cluster (`hex`), sidecar (`c_header`) — the **sidecar wins**
(`c_header`). `output_dir` is only set at the cluster level
(`build/sensors`) — that's what applies, since neither the sidecar nor
a CLI flag override it. `pld config show temp_sensor` prints the
origin of every resolved field, including `cluster (sensors)` for the
ones that came from there.

For a **batch table**, the same precedence applies with the batch's
own inline overrides (`[[batch_table]]`'s `reader`/`writer`/
`byte_order`/`stages`) playing the sidecar's role — see
[BATCH.md](BATCH.md).

---

## Managing clusters and tags

### CLI

```bash
pld cluster new sensors --writer hex --output-dir build/sensors
pld cluster list
pld cluster show sensors
pld cluster edit sensors --output-dir build/sensors2
pld cluster edit sensors --clear-output-dir       # remove that override
pld cluster assign temp_sensor sensors
pld cluster unassign temp_sensor
pld cluster delete sensors                        # refuses if it still has members
pld cluster delete sensors --force                # ...unless forced: members lose the cluster, keep their tags

pld tag temp_sensor                               # show current tags
pld tag temp_sensor --add prod --add beta
pld tag temp_sensor --remove beta
pld tags                                           # every tag in use project-wide, with a table count

pld build-all --cluster sensors                   # only build this cluster's tables
pld build-all --cluster sensors --filter "*.raw"   # combined: both narrow the set (AND)
```

`[cluster.plugin.*]` overrides aren't exposed as typed CLI flags (no
existing precedent for persisting plugin config from the CLI) — set
them via the web UI's cluster form, or hand-edit `table-tool.toml`.

### Web

The **Clusters** page lists every cluster (overrides, member count),
lets you create/edit/delete one, with the same "still has members"
refusal (and force-delete option) as the CLI. A table's cluster and
tags are edited from its own table page, in the "Tags & cluster" card.
The Dashboard gets a search box (matches name and tags) plus a cluster
filter (pick one) and tag filters (pick any number — a table matching
**any** active tag passes, not all of them: this is a quick-search
aid, not a precise query) once the project actually has clusters/tags
in use — a project that doesn't use this feature sees no extra UI.

---

## What happens on delete

- Deleting a table (`pld rm`, the web delete flow) also drops its
  `[[table_meta]]` entry — no orphaned cluster assignment or tags left
  pointing at a table that no longer exists.
- Deleting a cluster with members refuses by default (`ClusterError`,
  listing the members) — pass `--force` (CLI) / `force=true` (API) to
  proceed anyway: those tables lose the `cluster` field but **keep
  their tags**.
