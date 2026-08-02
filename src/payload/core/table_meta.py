"""
Table metadata: per-table cluster assignment (at most one) and
free-form tags, declared explicitly in [[table_meta]] in
table-tool.toml, keyed by table name (see
src/payload/docs/CLUSTERS.md).

Deliberately separate from [[batch_table]] (which declares a table's
existence) and sidecars (build-config overrides only): [[table_meta]]
is pure organizational metadata, applying uniformly to single-file and
batch tables alike — neither of the other two mechanisms has a natural
place to hold "which cluster" / "which tags" for a table that isn't
itself a batch.

The shallow parsing (known field names, base types) lives in
core/config.py, exactly like batch_tables/clusters; this module does
the "deep" part: turning the raw list into a name-indexed dict,
catching a duplicate [[table_meta]] name, and (when clusters are
given) a 'cluster' field naming a [[cluster]] that doesn't exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from payload.core.errors import ClusterError, TableMetaError

if TYPE_CHECKING:
    from pathlib import Path

    from payload.core.clusters import Cluster
    from payload.core.config import PayloadConfig


@dataclass
class TableMeta:
    name: str
    cluster: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    # free-form custom properties (key -> string), e.g. memory address,
    # version, author — surfaced to plugins at build time via
    # config["table_meta"]["properties"] (see pipeline.build) so a
    # reader can forward them to the writer through TableIR.extra
    properties: dict[str, str] = field(default_factory=dict)


def resolve_table_meta(
    project_root: "Path", config: "PayloadConfig", clusters: dict[str, "Cluster"] | None = None,
) -> dict[str, TableMeta]:
    """Expands config.table_meta (raw list of dicts, structurally
    validated only by core/config.py) into a name -> TableMeta dict.
    Raises TableMetaError on a duplicate [[table_meta]] name. If
    'clusters' is given (the caller passes resolve_clusters()'s
    result), also validates that every declared 'cluster' names an
    existing [[cluster]] — raises ClusterError otherwise. Pass
    clusters=None only when the cross-check has already been done (or
    is deliberately skipped) by the caller; every other caller should
    pass it. project_root is unused today, kept for signature symmetry
    with resolve_clusters/resolve_batch_tables."""
    table_metas: dict[str, TableMeta] = {}
    for entry in config.table_meta:
        name = entry["name"]
        if name in table_metas:
            raise TableMetaError(name, "duplicate name across multiple [[table_meta]]")

        cluster_name = entry.get("cluster") or None
        if clusters is not None and cluster_name is not None and cluster_name not in clusters:
            raise ClusterError(cluster_name, f"referenced by [[table_meta]] '{name}' but not declared")

        table_metas[name] = TableMeta(
            name=name,
            cluster=cluster_name,
            tags=list(entry.get("tags") or []),
            notes=str(entry.get("notes") or ""),
            properties={str(k): str(v) for k, v in (entry.get("properties") or {}).items()},
        )
    return table_metas
