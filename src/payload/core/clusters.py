"""
Clusters: a named bundle of config overrides a table can opt into (at
most one cluster per table, see src/payload/docs/CLUSTERS.md),
declared explicitly in [[cluster]] in table-tool.toml — never via
naming convention, for consistency with the approach already used for
[[batch_table]]/[pipeline.stages].

A cluster sits in the config-resolution chain between the global
[defaults] and a table's own sidecar/batch-inline overrides (see the
module docstring in core/config.py for the full four-tier order). It
can override 'defaults' and 'plugin' — deliberately NOT 'pipeline':
a cluster silently changing a multi-stage pipeline for a whole group
of tables is a bigger footgun than a defaults/plugin tweak, kept out
of v1 on purpose.

The shallow parsing (known field names, base types) lives in
core/config.py, exactly like batch_tables/pipeline_spec.py; this
module does the "deep" part: turning the raw list into a name-indexed
dict and catching a duplicate [[cluster]] name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from payload.core.errors import ClusterError

if TYPE_CHECKING:
    from pathlib import Path

    from payload.core.config import PayloadConfig


@dataclass
class Cluster:
    name: str
    defaults: dict = field(default_factory=dict)
    plugin: dict = field(default_factory=dict)


def resolve_clusters(project_root: "Path", config: "PayloadConfig") -> dict[str, Cluster]:
    """Expands config.clusters (raw list of dicts, structurally
    validated only by core/config.py) into a name -> Cluster dict.
    Raises ClusterError on a duplicate [[cluster]] name. project_root
    is unused today — kept for signature symmetry with
    resolve_batch_tables/resolve_table_meta, and because a future
    cluster-scoped filesystem check (e.g. validating a cluster's
    output_dir override) would need it."""
    clusters: dict[str, Cluster] = {}
    for entry in config.clusters:
        name = entry["name"]
        if name in clusters:
            raise ClusterError(name, "duplicate name across multiple [[cluster]]")
        clusters[name] = Cluster(
            name=name,
            defaults=dict(entry.get("defaults") or {}),
            plugin=dict(entry.get("plugin") or {}),
        )
    return clusters


def cluster_override_raw(cluster: Cluster | None) -> dict:
    """The cluster's overrides in the same {'defaults': {...},
    'plugin': {...}} raw shape core/config.py's deep_merge already
    expects — the one piece of "what does a cluster override" logic
    shared by both the single-file (raw-dict merge, see
    resolve_config_with_provenance) and batch (dataclass replace, see
    core/batch_tables.py's effective_config) resolution paths. {} if
    cluster is None or declares nothing in either section."""
    if cluster is None:
        return {}
    raw: dict = {}
    if cluster.defaults:
        raw["defaults"] = dict(cluster.defaults)
    if cluster.plugin:
        raw["plugin"] = dict(cluster.plugin)
    return raw
