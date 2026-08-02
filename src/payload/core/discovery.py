"""Discovery of table sources under a root. Shared by build-all and the
history system (status/commit must see exactly the same set of tables
discovered by the batch build, otherwise 'pld status' and 'pld
build-all' could disagree about what exists)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.batch_tables import BatchTable, effective_config, resolve_batch_tables
from payload.core.config import GLOBAL_CONFIG_FILENAME, SIDECAR_SUFFIX, load_config
from payload.core.errors import DuplicateTableNameError
from payload.core.local_plugins import PLUGINS_DIRNAME

if TYPE_CHECKING:
    from payload.core.clusters import Cluster
    from payload.core.config import PayloadConfig
    from payload.core.table_meta import TableMeta


def _resolve_or_self(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:  # pragma: no cover - defensive, matches the previous behavior
        return p


def _resolve_against_root(root: Path, p: Path) -> Path:
    # A relative output_dir/cache_dir (the common case — "build",
    # ".payload_cache") means "relative to the served project's root",
    # NEVER the calling process's cwd: 'pld serve /other/project' from
    # a different folder is a legitimate case (see web/paths.py), and
    # so is 'pld status --root /other/project' from the CLI. Resolving
    # bare Path(cfg.output_dir) against cwd silently produced a
    # nonexistent path in both cases — the output/cache dir exclusion
    # below would then never match anything.
    p = Path(p)
    return p if p.is_absolute() else root / p


def is_table_candidate(path: Path, root: Path, output_dir: Path, cache_dir: Path | None = None) -> bool:
    """Whether 'path' could be a table source — deliberately NOT
    gated on any reader recognizing its extension (a project starts
    with zero readers installed, see the no-bundled-plugins refactor:
    gating discovery on an installed reader made every imported file
    invisible until a matching plugin existed, defeating the whole
    "import now, install a reader later" model). What IS excluded is
    project infrastructure that's obviously never a table: the global
    config, sidecars, the output/cache dirs, the plugins/ folder, and
    any hidden file/dir (dotfiles, .git, editor swap files, ...)."""
    if not path.is_file():
        return False
    if path.name == GLOBAL_CONFIG_FILENAME or path.name.endswith(SIDECAR_SUFFIX):
        return False
    resolved_root = _resolve_or_self(root)
    try:
        rel_parts = path.resolve().relative_to(resolved_root).parts
    except (OSError, ValueError):
        rel_parts = path.parts
    if any(part.startswith(".") for part in rel_parts):
        return False
    if rel_parts and rel_parts[0] == PLUGINS_DIRNAME:
        return False

    resolved = _resolve_or_self(path)
    if _resolve_or_self(_resolve_against_root(root, output_dir)) in resolved.parents:
        return False
    if cache_dir is not None and _resolve_or_self(_resolve_against_root(root, cache_dir)) in resolved.parents:
        return False
    return True


def discover_table_sources(
    root: Path,
    output_dir: Path,
    cache_dir: Path | None = None,
    filter_glob: str | None = None,
) -> list[Path]:
    pattern = filter_glob or "**/*"
    if pattern.endswith("**"):
        # 'sensors/**' on its own matches ONLY the folder (documented
        # but counterintuitive pathlib.glob behavior), not the files
        # inside it — we normalize so the intuitive usage works.
        pattern = pattern + "/*"

    sources = [p for p in root.glob(pattern) if is_table_candidate(p, root, output_dir, cache_dir)]
    return sorted(sources)


def find_duplicate_stems(sources: list[Path]) -> dict[str, list[Path]]:
    """The table name (filename stem) is the identity used for build
    output/golden/history — two sources with the same stem in
    different folders silently collide on all three. Returns only the
    groups with more than one file (empty dict if there are no
    duplicates)."""
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for p in sources:
        by_stem[p.stem].append(p)
    return {name: paths for name, paths in by_stem.items() if len(paths) > 1}


def check_no_batch_name_collisions(sources: list[Path], batch_tables: list[BatchTable]) -> None:
    """A [[batch_table]] name that collides with a real source file's
    stem is the same ambiguity as two files with the same stem — both
    index build/golden/history by name."""
    batch_names = {bt.name for bt in batch_tables}
    duplicates = {
        p.stem: [p, f"[[batch_table]] '{p.stem}'"]
        for p in sources if p.stem in batch_names
    }
    if duplicates:
        raise DuplicateTableNameError(duplicates)


def exclude_batch_members(sources: list[Path], batch_tables: list[BatchTable]) -> list[Path]:
    """A file declared as a source of a [[batch_table]] must not ALSO
    show up as a standalone table in normal discovery — otherwise
    (e.g. sources = ["ROW*.txt"] with .txt recognized by a reader)
    each ROW1.txt/ROW2.txt would be discovered twice: once as part of
    the 'rows' batch, once as a standalone table 'ROW1'/'ROW2', with
    duplicated and confusing build/output."""
    member_paths = {p for bt in batch_tables for p in bt.source_paths}
    return [s for s in sources if s not in member_paths]


def discover_for_history(root: Path) -> tuple[list[Path], list[BatchTable], "PayloadConfig"]:
    """Shared helper for the CLI and the web UI: the exact same set of
    tables (single files + batch tables declared in [[batch_table]])
    that build-all would see, so 'pld status'/'pld commit' and the web
    dashboard never disagree about what exists."""
    config = load_config(root)
    sources = discover_table_sources(root, Path(config.defaults.output_dir), Path(config.defaults.cache_dir))

    duplicates = find_duplicate_stems(sources)
    if duplicates:
        raise DuplicateTableNameError(duplicates)

    batch_tables = resolve_batch_tables(root, config)
    sources = exclude_batch_members(sources, batch_tables)
    check_no_batch_name_collisions(sources, batch_tables)

    return sources, batch_tables, config


@dataclass
class TableRef:
    """Normalized reference to ONE table, regardless of whether it's a
    single file (source_paths has 1 element, is_batch=False,
    batch=None) or a batch table (N elements, is_batch=True, batch=the
    BatchTable with any reader/writer/byte_order/stages overrides) —
    see src/payload/docs/BATCH.md."""

    name: str
    source_paths: list[Path]
    is_batch: bool
    batch: BatchTable | None = None


def resolve_table_ref(
    sources: list[Path], batch_tables: list[BatchTable], table_name: str
) -> "TableRef | None":
    """Replaces the inline scan 'next((s for s in sources if s.stem ==
    table_name), None)' duplicated across CLI/web — now with a second
    branch for batch tables. Batch tables are checked first: their
    name is declared explicitly in config, not derived from a
    filename, so there's no ambiguity to resolve."""
    for bt in batch_tables:
        if bt.name == table_name:
            return TableRef(name=bt.name, source_paths=bt.source_paths, is_batch=True, batch=bt)
    for s in sources:
        if s.stem == table_name:
            return TableRef(name=table_name, source_paths=[s], is_batch=False)
    return None


def all_table_refs(sources: list[Path], batch_tables: list[BatchTable]) -> list[TableRef]:
    """All the project's tables as TableRef, same order as 'sources'
    followed by the batch tables — used by build-all/status/commit/
    report/export, which must iterate over ALL tables instead of
    resolving one by name."""
    refs = [TableRef(name=s.stem, source_paths=[s], is_batch=False) for s in sources]
    refs += [TableRef(name=bt.name, source_paths=bt.source_paths, is_batch=True, batch=bt) for bt in batch_tables]
    return refs


def resolve_table_config(
    root: Path,
    base_config: "PayloadConfig",
    ref: TableRef,
    clusters: dict[str, "Cluster"],
    table_metas: dict[str, "TableMeta"],
) -> "PayloadConfig":
    """The one call every report/status/build/config-show call site
    needs instead of hand-rolling 'effective_config(...) if ref.is_batch
    else load_config(...)' (duplicated at ~10 call sites) — resolves
    ref's full config, cluster override included, for either a
    single-file or a batch table. clusters/table_metas are resolved
    ONCE by the caller (e.g. once per /api/report request, once per
    run_batch_build call) — resolving them fresh per ref would be
    redundant TOML re-parsing, multiplied by run_batch_build's thread
    pool when jobs>1."""
    meta = table_metas.get(ref.name)
    cluster = clusters.get(meta.cluster) if meta and meta.cluster else None
    if ref.is_batch:
        return effective_config(base_config, ref.batch, cluster=cluster)
    return load_config(root, source_path=ref.source_paths[0], table_name=ref.name)
