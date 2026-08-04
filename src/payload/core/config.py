"""
Four-tier config, increasing precedence:
  1. global table-tool.toml (project root)
  2. the table's cluster, if it belongs to one (see core/clusters.py) —
     [[cluster]].defaults/[[cluster]].plugin
  3. per-table sidecar (<name>.config.toml next to the source), or a
     batch table's own inline overrides (core/batch_tables.py)
  4. CLI flags

The merge is "deep": each tier only overwrites the keys it declares,
not the whole section.

Validation done by hand with stdlib dataclasses, deliberately without
pydantic: pydantic-core is a Rust extension with no precompiled wheel
for several ARM platforms (e.g. Termux on Android), which broke
installation there. dataclasses + explicit validation is one fewer
compiled dependency, and makes the tool installable anywhere a plain
Python is available.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

from payload.core.clusters import cluster_override_raw, resolve_clusters
from payload.core.errors import BatchTableError, ClusterError, InvalidConfigError
from payload.core.table_meta import resolve_table_meta

logger = logging.getLogger(__name__)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

GLOBAL_CONFIG_FILENAME = "table-tool.toml"
SIDECAR_SUFFIX = ".config.toml"


@dataclass
class ProjectConfig:
    # "" = no explicit name (projects created before this field
    # existed, or a hand-written table-tool.toml) — whoever displays
    # the name (web/CLI) decides the fallback on its own (e.g. the
    # folder name), the core config doesn't invent one here so as not
    # to hide the fact that the data is missing.
    name: str = ""
    description: str = ""


@dataclass
class DefaultsConfig:
    writer: str | None = None  # None = no explicit preference: the reader may suggest one
    reader: str | None = None  # None = auto-resolved from extension/sniff (see PluginRegistry.find_reader)
    output_dir: str = "build"
    cache_dir: str = ".payload_cache"
    byte_order: str = "little"  # "little" | "big" — target for readers/writers that handle multi-byte values


@dataclass
class PayloadConfig:
    project: ProjectConfig = field(default_factory=ProjectConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    # Plugin territory, not the core's: [plugin.<name>] in
    # table-tool.toml/sidecar ends up here with no schema validation —
    # the core has no way to know what a third-party plugin needs. A
    # reader/writer reads its own slice with
    # config.get("plugin", {}).get(self.name, {}).
    plugin: dict = field(default_factory=dict)
    # [pipeline] stages = [...] — raw list of dicts, validated and
    # turned into a PipelineSpec by core/pipeline_spec.py, not here
    # (the core config stays generic, it shouldn't know the
    # reader/writer/exec alternation rules). Empty = no explicit
    # pipeline, falls back to implicit resolution from --from/--to
    # (see core/pipeline.py).
    pipeline_stages: list = field(default_factory=list)
    # [[batch_table]] — raw list of dicts (TOML array of tables), a
    # logical table built from SEVERAL source files instead of just
    # one (see src/payload/docs/BATCH.md). Only structurally validated
    # here (known field names, base types); expanding the 'sources'
    # globs and deep validation (duplicate names, missing paths) is
    # core/batch_tables.py's territory, not the core config's — same
    # boundary already drawn for pipeline_stages/pipeline_spec.py.
    batch_tables: list = field(default_factory=list)
    # [[cluster]] — raw list of dicts, a named bundle of
    # defaults/plugin overrides a table can opt into (see
    # src/payload/docs/CLUSTERS.md). Only structurally validated here;
    # duplicate-name checking is core/clusters.py's territory, same
    # boundary as batch_tables.
    clusters: list = field(default_factory=list)
    # [[table_meta]] — raw list of dicts: per-table 'cluster'
    # assignment (at most one) and free-form 'tags', keyed by table
    # name. Pure organizational metadata, not build config — doesn't
    # declare a table's existence (unlike [[batch_table]]) or override
    # its build config directly (unlike a sidecar); it only points at
    # a [[cluster]] which does. Only structurally validated here;
    # duplicate-name/dangling-cluster-reference checking is
    # core/table_meta.py's territory.
    table_meta: list = field(default_factory=list)

    def model_dump(self) -> dict:
        """Method name deliberately kept the same as pydantic v2: no
        other module (pipeline, cli, doctor) needs to know pydantic
        isn't underneath anymore."""
        return asdict(self)


_PROJECT_STR_FIELDS = ("name", "description")
_DEFAULTS_STR_FIELDS = ("writer", "reader", "output_dir", "cache_dir", "byte_order")
_VALID_BYTE_ORDERS = ("little", "big")
_BATCH_TABLE_STR_FIELDS = ("name", "reader", "writer", "byte_order")
_BATCH_TABLE_LIST_STR_FIELDS = ("sources",)
_TABLE_META_STR_FIELDS = ("name", "cluster", "notes")
_TABLE_META_LIST_STR_FIELDS = ("tags",)
_TABLE_META_DICT_STR_FIELDS = ("properties",)


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise InvalidConfigError(path, field="<root>", reason=str(e)) from e


def deep_merge(base: dict, override: dict) -> dict:
    """Not prefixed with '_': core/clusters.py and core/batch_tables.py
    reuse this instead of reimplementing dict-merge for a cluster's
    [plugin.*] override."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_section(
    section: dict, section_name: str, str_fields: tuple, list_str_fields: tuple, path: Path,
    dict_str_fields: tuple = (),
) -> None:
    known = set(str_fields) | set(list_str_fields) | set(dict_str_fields)
    for key, value in section.items():
        field_path = f"{section_name}.{key}"
        if key not in known:
            raise InvalidConfigError(path, field=field_path, reason="unknown field")
        if key in str_fields and not isinstance(value, str):
            raise InvalidConfigError(
                path, field=field_path,
                reason=f"must be a string, got {type(value).__name__}",
            )
        if key in list_str_fields:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise InvalidConfigError(path, field=field_path, reason="must be a list of strings")
        if key in dict_str_fields:
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                raise InvalidConfigError(path, field=field_path, reason="must be a dict of strings")


def _validate_batch_table_entry(entry: dict, index: int, path: Path) -> None:
    field_prefix = f"batch_table[{index}]"
    if not isinstance(entry, dict):
        raise InvalidConfigError(path, field=field_prefix, reason="must be a TOML table, e.g. [[batch_table]]")
    if not entry.get("name"):
        raise InvalidConfigError(path, field=f"{field_prefix}.name", reason="required, can't be empty")
    if not entry.get("sources"):
        raise InvalidConfigError(path, field=f"{field_prefix}.sources", reason="required, can't be empty")
    _validate_section(
        {k: v for k, v in entry.items() if k != "stages"},
        field_prefix, _BATCH_TABLE_STR_FIELDS, _BATCH_TABLE_LIST_STR_FIELDS, path,
    )
    stages = entry.get("stages", [])
    if not isinstance(stages, list):
        raise InvalidConfigError(path, field=f"{field_prefix}.stages", reason="must be a list of stages")


def _validate_cluster_entry(entry: dict, index: int, path: Path) -> None:
    field_prefix = f"cluster[{index}]"
    if not isinstance(entry, dict):
        raise InvalidConfigError(path, field=field_prefix, reason="must be a TOML table, e.g. \\[\\[cluster]]")
    if not entry.get("name"):
        raise InvalidConfigError(path, field=f"{field_prefix}.name", reason="required, can't be empty")

    unknown = set(entry.keys()) - {"name", "defaults", "plugin"}
    if unknown:
        raise InvalidConfigError(path, field=f"{field_prefix}.{next(iter(unknown))}", reason="unknown field")

    cluster_defaults = entry.get("defaults", {})
    if not isinstance(cluster_defaults, dict):
        raise InvalidConfigError(path, field=f"{field_prefix}.defaults", reason="must be a TOML table")
    _validate_section(cluster_defaults, f"{field_prefix}.defaults", _DEFAULTS_STR_FIELDS, (), path)
    byte_order = cluster_defaults.get("byte_order")
    if byte_order is not None and byte_order not in _VALID_BYTE_ORDERS:
        raise InvalidConfigError(
            path, field=f"{field_prefix}.defaults.byte_order",
            reason=f"must be 'little' or 'big', got '{byte_order}'",
        )

    # [plugin.*] is deliberately NOT validated, same as the top-level
    # [plugin.*] — plugin territory, the core has no way to know which
    # keys are legitimate.
    cluster_plugin = entry.get("plugin", {})
    if not isinstance(cluster_plugin, dict):
        raise InvalidConfigError(path, field=f"{field_prefix}.plugin", reason="must be a TOML table")


def _validate_table_meta_entry(entry: dict, index: int, path: Path) -> None:
    field_prefix = f"table_meta[{index}]"
    if not isinstance(entry, dict):
        raise InvalidConfigError(path, field=field_prefix, reason="must be a TOML table, e.g. \\[\\[table_meta]]")
    if not entry.get("name"):
        raise InvalidConfigError(path, field=f"{field_prefix}.name", reason="required, can't be empty")
    _validate_section(entry, field_prefix, _TABLE_META_STR_FIELDS, _TABLE_META_LIST_STR_FIELDS, path, _TABLE_META_DICT_STR_FIELDS)


def _build_config(merged: dict, path: Path) -> PayloadConfig:
    project_dict = merged.get("project", {})
    defaults_dict = merged.get("defaults", {})
    plugin_dict = merged.get("plugin", {})
    pipeline_dict = merged.get("pipeline", {})
    batch_table_list = merged.get("batch_table", [])
    cluster_list = merged.get("cluster", [])
    table_meta_list = merged.get("table_meta", [])

    if not isinstance(project_dict, dict):
        raise InvalidConfigError(path, field="project", reason="must be a TOML table, e.g. [project]")
    if not isinstance(defaults_dict, dict):
        raise InvalidConfigError(path, field="defaults", reason="must be a TOML table, e.g. [defaults]")
    if not isinstance(plugin_dict, dict):
        raise InvalidConfigError(path, field="plugin", reason="must be a TOML table, e.g. [plugin.plugin_name]")
    if not isinstance(pipeline_dict, dict):
        raise InvalidConfigError(path, field="pipeline", reason="must be a TOML table, e.g. [pipeline]")
    if not isinstance(batch_table_list, list):
        raise InvalidConfigError(path, field="batch_table", reason="must be a list of [[batch_table]] tables")
    if not isinstance(cluster_list, list):
        raise InvalidConfigError(path, field="cluster", reason="must be a list of \\[\\[cluster]] tables")
    if not isinstance(table_meta_list, list):
        raise InvalidConfigError(path, field="table_meta", reason="must be a list of \\[\\[table_meta]] tables")

    pipeline_stages = pipeline_dict.get("stages", [])
    if not isinstance(pipeline_stages, list):
        raise InvalidConfigError(path, field="pipeline.stages", reason="must be a list of stages")

    unknown_top = set(merged.keys()) - {
        "project", "defaults", "plugin", "pipeline", "batch_table", "cluster", "table_meta",
    }
    if unknown_top:
        raise InvalidConfigError(path, field=next(iter(unknown_top)), reason="unknown section")

    _validate_section(project_dict, "project", _PROJECT_STR_FIELDS, (), path)
    _validate_section(defaults_dict, "defaults", _DEFAULTS_STR_FIELDS, (), path)
    # [plugin.*] is deliberately NOT validated here: it's plugin
    # territory, the core has no way to know which keys are legitimate.
    # [pipeline.stages] is deliberately validated only structurally
    # (list yes/no): the reader/writer/exec alternation rules are
    # core/pipeline_spec.py's territory, not the core config's. Same
    # boundary for [[batch_table]]/[[cluster]]/[[table_meta]]: only
    # structure/known fields here, not deep validation (duplicate
    # names, dangling references — core/batch_tables.py,
    # core/clusters.py, core/table_meta.py respectively).
    for i, entry in enumerate(batch_table_list):
        _validate_batch_table_entry(entry, i, path)
    for i, entry in enumerate(cluster_list):
        _validate_cluster_entry(entry, i, path)
    for i, entry in enumerate(table_meta_list):
        _validate_table_meta_entry(entry, i, path)

    byte_order = defaults_dict.get("byte_order", "little")
    if byte_order not in _VALID_BYTE_ORDERS:
        raise InvalidConfigError(
            path, field="defaults.byte_order",
            reason=f"must be 'little' or 'big', got '{byte_order}'",
        )

    return PayloadConfig(
        project=ProjectConfig(**project_dict),
        defaults=DefaultsConfig(**defaults_dict),
        plugin=plugin_dict,
        pipeline_stages=pipeline_stages,
        batch_tables=batch_table_list,
        clusters=cluster_list,
        table_meta=table_meta_list,
    )


def load_config(
    project_root: Path, source_path: Path | None = None, table_name: str | None = None,
) -> PayloadConfig:
    """Loads the global config, applies the table's cluster (if any)
    and then its sidecar on top if source_path is given, validates the
    result. table_name identifies which table's cluster to look up —
    if omitted but source_path is given, it defaults to
    source_path.stem (the table's name for every single-file table)."""
    config, _ = resolve_config_with_provenance(project_root, source_path, table_name)
    return config


def _flatten_keys(data: dict, prefix: str = "") -> list[str]:
    keys = []
    for k, v in data.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
        else:
            keys.append(full)
    return keys


def resolve_config_with_provenance(
    project_root: Path, source_path: Path | None = None, table_name: str | None = None,
) -> tuple[PayloadConfig, dict[str, str]]:
    """Like load_config, but also returns where each value comes from
    ('default' | 'global (table-tool.toml)' | 'cluster (<name>)' |
    'sidecar (<file>)') — used by 'pld config show' to debug the
    four-tier resolution."""
    merged: dict = {}
    provenance: dict[str, str] = {}

    global_path = project_root / GLOBAL_CONFIG_FILENAME
    if global_path.exists():
        logger.debug("Global config found: %s", global_path)
        global_data = _load_toml(global_path)
        merged = global_data
        for key in _flatten_keys(global_data):
            provenance[key] = f"global ({GLOBAL_CONFIG_FILENAME})"
    else:
        logger.debug("No global config at %s, using defaults", global_path)

    # Cluster resolution only happens when a specific table is being
    # resolved (there's nothing to look up otherwise) — same
    # "shallow-parsed here always, deep-resolved only when actually
    # needed" boundary [[batch_table]] already has (resolve_batch_tables
    # isn't called by every load_config either). Always resolved from
    # the GLOBAL data only, never from a sidecar: cluster membership is
    # a project-wide concept, a single table's sidecar has no business
    # defining or reassigning it.
    resolved_name = table_name if table_name is not None else (source_path.stem if source_path else None)
    if resolved_name is not None:
        global_config = _build_config(merged, global_path)
        clusters = resolve_clusters(project_root, global_config)
        table_metas = resolve_table_meta(project_root, global_config, clusters)
        meta = table_metas.get(resolved_name)
        cluster = clusters.get(meta.cluster) if meta and meta.cluster else None
        cluster_raw = cluster_override_raw(cluster)
        if cluster_raw:
            logger.debug("Cluster '%s' applied for %s", cluster.name, resolved_name)
            merged = deep_merge(merged, cluster_raw)
            for key in _flatten_keys(cluster_raw):
                provenance[key] = f"cluster ({cluster.name})"

    if source_path is not None:
        sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
        if sidecar_path.exists():
            sidecar_data = _load_toml(sidecar_path)
            logger.debug(
                "Sidecar applied for %s: %s (keys: %s)",
                source_path.name, sidecar_path.name, list(sidecar_data.keys()),
            )
            merged = deep_merge(merged, sidecar_data)
            for key in _flatten_keys(sidecar_data):
                provenance[key] = f"sidecar ({sidecar_path.name})"

    config = _build_config(merged, global_path)

    # every field untouched by global/sidecar comes from the dataclass default
    for section_name, section_obj in (("defaults", config.defaults),):
        for f in fields(section_obj):
            key = f"{section_name}.{f.name}"
            provenance.setdefault(key, "default")

    logger.debug(
        "Config resolved for %s: writer=%s",
        source_path.name if source_path else "<global>",
        config.defaults.writer,
    )
    return config, provenance


def config_schema() -> dict:
    """Schema of the editable 'defaults' fields, in the dataclass's
    declaration order — used by the web UI to generate the config form
    without duplicating the field list on the JS side. Anything a
    plugin needs (e.g. a compiler/toolchain path) lives in
    [plugin.<name>] instead, which the core deliberately doesn't
    validate or expose a form for (see PayloadConfig.plugin)."""
    def _section(cls, list_fields: tuple) -> list[dict]:
        return [{"key": f.name, "type": "list" if f.name in list_fields else "string"} for f in fields(cls)]

    return {
        "defaults": _section(DefaultsConfig, ()),
    }


def _drop_none_values(section: dict) -> dict:
    """'defaults.writer' is the only field with a None default ('no
    explicit preference') — a form that submits it empty sends None,
    which must be treated as "key absent", not as an invalid string
    value, otherwise _validate_section would reject it (it only
    expects str for the *_STR_FIELDS fields)."""
    return {k: v for k, v in section.items() if v is not None}


def write_global_config(project_root: Path, defaults: dict | None = None, plugin: dict | None = None) -> Path:
    """Writes/overwrites the [defaults] (and optionally [plugin.*])
    sections of table-tool.toml, preserving anything else as is.
    Validates BEFORE writing: a config that wouldn't pass load_config()
    never ends up on disk."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged = dict(_load_toml(path)) if path.exists() else {}
    if defaults is not None:
        merged["defaults"] = _drop_none_values(defaults)
    if plugin is not None:
        plugin_section = dict(merged.get("plugin", {}))
        for name, values in plugin.items():
            if not values:
                plugin_section.pop(name, None)
                continue
            section = dict(plugin_section.get(name, {}))
            for key, value in values.items():
                if value is None or value == "":
                    section.pop(key, None)
                else:
                    section[key] = value
            if section:
                plugin_section[name] = section
            else:
                plugin_section.pop(name, None)
        if plugin_section:
            merged["plugin"] = plugin_section
        else:
            merged.pop("plugin", None)

    _build_config(merged, path)

    path.write_text(tomli_w.dumps(merged))
    logger.debug("Global config written: %s", path)
    return path


def read_raw_sidecar(source_path: Path) -> dict:
    """Raw TOML of source_path's sidecar, or {} if it doesn't exist —
    used by the web UI to prefill the form with only the fields
    actually overridden (not the whole resolved config)."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    if not sidecar_path.exists():
        return {}
    return _load_toml(sidecar_path)


def write_sidecar_config(
    source_path: Path,
    defaults: dict | None = None,
    pipeline_stages: list | None = None,
    plugin: dict | None = None,
) -> Path:
    """Updates source_path's sidecar one piece at a time: each None
    parameter leaves the existing section untouched, an empty
    dict/list removes it (disables the override without touching the
    rest of the sidecar, e.g. a hand-written [plugin.*]). If the
    resulting sidecar is completely empty, the file is deleted instead
    of leaving an empty TOML on disk."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    merged = dict(_load_toml(sidecar_path)) if sidecar_path.exists() else {}

    if defaults is not None:
        defaults = _drop_none_values(defaults)
        if defaults:
            merged["defaults"] = defaults
        else:
            merged.pop("defaults", None)
    if pipeline_stages is not None:
        if pipeline_stages:
            merged["pipeline"] = {**merged.get("pipeline", {}), "stages": pipeline_stages}
        else:
            merged.pop("pipeline", None)
    if plugin is not None:
        # [plugin.<name>] — merge per plugin name; a key set to None/"" is removed
        plugin_section = dict(merged.get("plugin", {}))
        for name, values in plugin.items():
            if not values:
                plugin_section.pop(name, None)
                continue
            section = dict(plugin_section.get(name, {}))
            for key, value in values.items():
                if value is None or value == "":
                    section.pop(key, None)
                else:
                    section[key] = value
            if section:
                plugin_section[name] = section
            else:
                plugin_section.pop(name, None)
        if plugin_section:
            merged["plugin"] = plugin_section
        else:
            merged.pop("plugin", None)

    # Same structural validation as _build_config for defaults
    # (pipeline.stages has already been validated by the caller with
    # PipelineSpec.from_raw_stages before getting here — the
    # reader/writer/exec alternation rules aren't this module's
    # territory).
    _validate_section(merged.get("defaults", {}), "defaults", _DEFAULTS_STR_FIELDS, (), sidecar_path)
    byte_order = merged.get("defaults", {}).get("byte_order")
    if byte_order is not None and byte_order not in _VALID_BYTE_ORDERS:
        raise InvalidConfigError(
            sidecar_path, field="defaults.byte_order",
            reason=f"must be 'little' or 'big', got '{byte_order}'",
        )

    if not merged:
        if sidecar_path.exists():
            sidecar_path.unlink()
        logger.debug("Sidecar removed (empty after update): %s", sidecar_path)
        return sidecar_path

    sidecar_path.write_text(tomli_w.dumps(merged))
    logger.debug("Sidecar written: %s", sidecar_path)
    return sidecar_path


def delete_sidecar_config(source_path: Path) -> bool:
    """Deletes source_path's sidecar if it exists. Returns True if a
    file was actually removed (idempotent: False if there was nothing
    to delete already)."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    if sidecar_path.exists():
        sidecar_path.unlink()
        return True
    return False


# --- programmatic mutation of [[batch_table]] -------------------------------
#
# Writing used by the import of external tables (core/table_admin.py):
# creating a new [[batch_table]], growing an existing one with one more
# member, or removing one. Same pattern as
# write_global_config/write_sidecar_config — loads the raw TOML,
# mutates only the relevant part, validates with _build_config BEFORE
# writing (a config that wouldn't pass load_config() never ends up on
# disk), rewrites everything else unchanged.

def _read_full_config(path: Path) -> dict:
    return dict(_load_toml(path)) if path.exists() else {}


def _write_full_config(path: Path, merged: dict) -> None:
    _build_config(merged, path)
    path.write_text(tomli_w.dumps(merged))


def _read_batch_table_list(path: Path) -> tuple[dict, list[dict]]:
    merged = _read_full_config(path)
    return merged, list(merged.get("batch_table", []))


def _write_batch_table_list(path: Path, merged: dict, batch_table_list: list[dict]) -> None:
    merged["batch_table"] = batch_table_list
    _write_full_config(path, merged)


def create_batch_table(
    project_root: Path, name: str, sources: list[str],
    reader: str | None = None, writer: str | None = None, byte_order: str | None = None,
    stages: list | None = None,
) -> Path:
    """Adds a new [[batch_table]] to table-tool.toml — used when the
    user imports several files together as a new batch table. Fails
    if a [[batch_table]] with this name already exists."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, batch_table_list = _read_batch_table_list(path)
    if any(e.get("name") == name for e in batch_table_list):
        raise BatchTableError(name, "a [[batch_table]] with this name already exists")

    entry: dict = {"name": name, "sources": list(sources)}
    if reader:
        entry["reader"] = reader
    if writer:
        entry["writer"] = writer
    if byte_order:
        entry["byte_order"] = byte_order
    if stages:
        entry["stages"] = list(stages)

    batch_table_list.append(entry)
    _write_batch_table_list(path, merged, batch_table_list)
    logger.debug("[[batch_table]] '%s' created in %s", name, path)
    return path


def add_batch_table_source(project_root: Path, name: str, source: str) -> Path:
    """Appends a path to an existing [[batch_table]]'s 'sources' —
    used when the user imports one more file onto an already declared
    batch table. Idempotent: if the path is already there, it's not
    duplicated."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, batch_table_list = _read_batch_table_list(path)
    entry = next((e for e in batch_table_list if e.get("name") == name), None)
    if entry is None:
        raise BatchTableError(name, "no [[batch_table]] with this name")

    sources = list(entry.get("sources", []))
    if source not in sources:
        sources.append(source)
    entry["sources"] = sources

    _write_batch_table_list(path, merged, batch_table_list)
    logger.debug("[[batch_table]] '%s': added source '%s'", name, source)
    return path


def remove_batch_table_source(project_root: Path, name: str, source: str) -> bool:
    """Removes a literal path from a [[batch_table]]'s 'sources' —
    only for literal entries: a path matched by a glob pattern simply
    stops showing up on its own once the file is deleted from disk,
    there's nothing to edit in config for that case (see
    core/table_admin.py). True if the list was actually modified."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    if not path.exists():
        return False
    merged, batch_table_list = _read_batch_table_list(path)
    entry = next((e for e in batch_table_list if e.get("name") == name), None)
    if entry is None or source not in entry.get("sources", []):
        return False

    entry["sources"] = [s for s in entry["sources"] if s != source]
    _write_batch_table_list(path, merged, batch_table_list)
    logger.debug("[[batch_table]] '%s': removed source '%s'", name, source)
    return True


def remove_batch_table_entry(project_root: Path, name: str) -> bool:
    """Removes an entire [[batch_table]] from table-tool.toml — used
    when the last member file is deleted (a batch table with 0 files
    makes no sense). True if an entry was actually removed."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    if not path.exists():
        return False
    merged, batch_table_list = _read_batch_table_list(path)
    new_list = [e for e in batch_table_list if e.get("name") != name]
    if len(new_list) == len(batch_table_list):
        return False

    _write_batch_table_list(path, merged, new_list)
    logger.debug("[[batch_table]] '%s' removed", name)
    return True


def upsert_batch_table(
    project_root: Path,
    name: str,
    sources: list[str],
    reader: str | None = None,
    writer: str | None = None,
    byte_order: str | None = None,
    stages: list | None = None,
) -> Path:
    """Creates the [[batch_table]] if missing, otherwise replaces its
    'sources' and sets/clears the optional reader/writer/byte_order —
    the web editor's whole-list save (the CLI adds members one at a
    time through add_batch_table_source, the web replaces the list).
    None for reader/writer/byte_order leaves the field untouched;
    "" explicitly clears it."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, batch_table_list = _read_batch_table_list(path)
    entry = next((e for e in batch_table_list if e.get("name") == name), None)
    if entry is None:
        entry = {"name": name, "sources": list(sources)}
        batch_table_list.append(entry)
    else:
        entry["sources"] = list(sources)
    for field, value in (("reader", reader), ("writer", writer), ("byte_order", byte_order), ("stages", stages)):
        if value is None:
            continue
        if value:
            entry[field] = list(value) if field == "stages" else value
        else:
            entry.pop(field, None)
    _write_batch_table_list(path, merged, batch_table_list)
    logger.debug("[[batch_table]] '%s' upserted (%d sources)", name, len(sources))
    return path


# --- programmatic mutation of [[cluster]] / [[table_meta]] ------------------
#
# Same load-mutate-validate-write pattern as [[batch_table]] above.

def _read_cluster_list(path: Path) -> tuple[dict, list[dict]]:
    merged = _read_full_config(path)
    return merged, list(merged.get("cluster", []))


def _write_cluster_list(path: Path, merged: dict, cluster_list: list[dict]) -> None:
    merged["cluster"] = cluster_list
    _write_full_config(path, merged)


def _read_table_meta_list(path: Path) -> tuple[dict, list[dict]]:
    merged = _read_full_config(path)
    return merged, list(merged.get("table_meta", []))


def _write_table_meta_list(path: Path, merged: dict, table_meta_list: list[dict]) -> None:
    merged["table_meta"] = table_meta_list
    _write_full_config(path, merged)


def create_cluster(
    project_root: Path, name: str, defaults: dict | None = None, plugin: dict | None = None,
) -> Path:
    """Adds a new [[cluster]] to table-tool.toml. Fails if a
    [[cluster]] with this name already exists."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, cluster_list = _read_cluster_list(path)
    if any(e.get("name") == name for e in cluster_list):
        raise ClusterError(name, "a \\[\\[cluster]] with this name already exists")

    entry: dict = {"name": name}
    defaults = _drop_none_values(defaults) if defaults else {}
    if defaults:
        entry["defaults"] = defaults
    if plugin:
        entry["plugin"] = plugin

    cluster_list.append(entry)
    _write_cluster_list(path, merged, cluster_list)
    logger.debug("[[cluster]] '%s' created in %s", name, path)
    return path


def update_cluster(
    project_root: Path, name: str, defaults: dict | None = None, plugin: dict | None = None,
) -> Path:
    """Replaces an existing [[cluster]]'s 'defaults'/'plugin' section
    wholesale — None leaves that section untouched, an empty dict (or
    one that becomes empty after dropping None values) clears it, same
    convention as write_sidecar_config. Raises ClusterError if no
    [[cluster]] with this name exists."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, cluster_list = _read_cluster_list(path)
    entry = next((e for e in cluster_list if e.get("name") == name), None)
    if entry is None:
        raise ClusterError(name, "no \\[\\[cluster]] with this name")

    if defaults is not None:
        defaults = _drop_none_values(defaults)
        if defaults:
            entry["defaults"] = defaults
        else:
            entry.pop("defaults", None)
    if plugin is not None:
        if plugin:
            entry["plugin"] = plugin
        else:
            entry.pop("plugin", None)

    _write_cluster_list(path, merged, cluster_list)
    logger.debug("[[cluster]] '%s' updated", name)
    return path


def delete_cluster(project_root: Path, name: str, *, force: bool = False) -> bool:
    """Removes a [[cluster]]. If any [[table_meta]] entry still
    references it, refuses with ClusterError unless force=True, in
    which case those entries have their 'cluster' field cleared (tags
    kept — the entry itself is only dropped if it ends up with neither
    a cluster nor tags, same rule set_table_cluster/set_table_tags
    already follow). True if a cluster was actually removed."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    if not path.exists():
        return False
    merged = _read_full_config(path)
    cluster_list = list(merged.get("cluster", []))
    if not any(e.get("name") == name for e in cluster_list):
        return False

    table_meta_list = list(merged.get("table_meta", []))
    members = [e.get("name") for e in table_meta_list if e.get("cluster") == name]
    if members and not force:
        raise ClusterError(name, f"still has {len(members)} member table(s): {', '.join(members)}")

    new_table_meta_list = []
    for e in table_meta_list:
        if e.get("cluster") == name:
            e = dict(e)
            e.pop("cluster", None)
            if not e.get("tags"):
                continue  # nothing left to declare about this table
        new_table_meta_list.append(e)

    merged["cluster"] = [e for e in cluster_list if e.get("name") != name]
    merged["table_meta"] = new_table_meta_list
    _write_full_config(path, merged)
    logger.debug("[[cluster]] '%s' removed (force=%s, %d member(s) cleared)", name, force, len(members))
    return True


def _upsert_table_meta(table_meta_list: list[dict], name: str) -> dict:
    """Returns the [[table_meta]] entry for 'name', creating (and
    appending) an empty one if it doesn't exist yet — mutates
    table_meta_list in place."""
    entry = next((e for e in table_meta_list if e.get("name") == name), None)
    if entry is None:
        entry = {"name": name}
        table_meta_list.append(entry)
    return entry


def _drop_table_meta_if_empty(table_meta_list: list[dict], entry: dict) -> list[dict]:
    if not entry.get("cluster") and not entry.get("tags") and not entry.get("notes") and not entry.get("properties"):
        return [e for e in table_meta_list if e is not entry]
    return table_meta_list


def set_table_cluster(project_root: Path, table_name: str, cluster: str | None) -> Path:
    """Sets (cluster=str) or clears (cluster=None) table_name's
    cluster, creating its [[table_meta]] entry if missing, removing it
    again if the result has neither a cluster nor tags. Validates the
    target cluster exists BEFORE writing (ClusterError otherwise)."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, table_meta_list = _read_table_meta_list(path)

    if cluster is not None:
        cluster_list = list(merged.get("cluster", []))
        if not any(e.get("name") == cluster for e in cluster_list):
            raise ClusterError(cluster, "no \\[\\[cluster]] with this name")

    entry = _upsert_table_meta(table_meta_list, table_name)
    if cluster:
        entry["cluster"] = cluster
    else:
        entry.pop("cluster", None)
    table_meta_list = _drop_table_meta_if_empty(table_meta_list, entry)

    _write_table_meta_list(path, merged, table_meta_list)
    logger.debug("[[table_meta]] '%s': cluster set to %r", table_name, cluster)
    return path


def set_table_tags(project_root: Path, table_name: str, tags: list[str]) -> Path:
    """Whole-list replace (empty list clears all tags) — de-duplicates
    while preserving order. Creates/removes the [[table_meta]] entry
    the same way set_table_cluster does. This is the SOLE tag-mutation
    primitive: the CLI's --add/--remove and the web tag editor both
    read the table's current tags first and call this with the full
    computed list, rather than having separate add/remove-at-the-
    config-layer helpers with subtly different idempotency semantics."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, table_meta_list = _read_table_meta_list(path)
    deduped = list(dict.fromkeys(tags))

    entry = _upsert_table_meta(table_meta_list, table_name)
    if deduped:
        entry["tags"] = deduped
    else:
        entry.pop("tags", None)
    table_meta_list = _drop_table_meta_if_empty(table_meta_list, entry)

    _write_table_meta_list(path, merged, table_meta_list)
    logger.debug("[[table_meta]] '%s': tags set to %s", table_name, deduped)
    return path


def set_table_meta_fields(
    project_root: Path,
    table_name: str,
    notes: str | None = None,
    properties: dict[str, str] | None = None,
) -> Path:
    """Sets/clears a table's free-form notes and/or custom properties
    (key -> string, e.g. a memory address, metadata, ...). None = leave
    untouched; "" / {} = clear. Same [[table_meta]] upsert/drop rules as
    set_table_tags: the entry is created if missing and removed again
    when no cluster/tags/notes/properties remain."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged, table_meta_list = _read_table_meta_list(path)

    entry = _upsert_table_meta(table_meta_list, table_name)
    if notes is not None:
        if notes:
            entry["notes"] = str(notes)
        else:
            entry.pop("notes", None)
    if properties is not None:
        cleaned = {str(k): str(v) for k, v in properties.items() if k}
        if cleaned:
            entry["properties"] = cleaned
        else:
            entry.pop("properties", None)
    table_meta_list = _drop_table_meta_if_empty(table_meta_list, entry)

    _write_table_meta_list(path, merged, table_meta_list)
    logger.debug("[[table_meta]] '%s': notes/properties updated", table_name)
    return path


def remove_table_meta_entry(project_root: Path, table_name: str) -> bool:
    """Removes a table's entire [[table_meta]] entry — used by
    core/table_admin.py when a table is deleted, mirroring
    remove_batch_table_entry. Idempotent."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    if not path.exists():
        return False
    merged, table_meta_list = _read_table_meta_list(path)
    new_list = [e for e in table_meta_list if e.get("name") != table_name]
    if len(new_list) == len(table_meta_list):
        return False

    _write_table_meta_list(path, merged, new_list)
    logger.debug("[[table_meta]] '%s' removed", table_name)
    return True
