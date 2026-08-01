"""Discovery of table sources under a root. Shared by build-all and the
history system (status/commit must see exactly the same set of tables
discovered by the batch build, otherwise 'pld status' and 'pld
build-all' could disagree about what exists)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.batch_tables import BatchTable, resolve_batch_tables
from payload.core.config import load_config
from payload.core.errors import DuplicateTableNameError
from payload.core.registry import load_plugins

if TYPE_CHECKING:
    from payload.core.config import PayloadConfig


def discover_table_sources(
    root: Path,
    known_extensions: set[str],
    output_dir: Path,
    filter_glob: str | None = None,
) -> list[Path]:
    pattern = filter_glob or "**/*"
    if pattern.endswith("**"):
        # 'sensors/**' on its own matches ONLY the folder (documented
        # but counterintuitive pathlib.glob behavior), not the files
        # inside it — we normalize so the intuitive usage works.
        pattern = pattern + "/*"
    try:
        resolved_output = output_dir.resolve()
    except OSError:
        resolved_output = output_dir

    sources = []
    for p in root.glob(pattern):
        if not p.is_file() or p.suffix not in known_extensions:
            continue
        try:
            if resolved_output in p.resolve().parents:
                continue
        except OSError:
            pass
        sources.append(p)
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
    registry = load_plugins(project_root=root)
    config = load_config(root)
    known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
    sources = discover_table_sources(root, known_ext, Path(config.defaults.output_dir))

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
