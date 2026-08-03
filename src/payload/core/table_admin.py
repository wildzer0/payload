"""
Table lifecycle operations that don't go through the normal
build/commit path: importing an external file (a new table, or
updating the source of one already tracked) and deleting (source +
output + cache, NEVER the history — see src/payload/docs/USAGE.md,
'Table management' section).

Explicit consent (user confirmation, overwrite, etc.) is the caller's
responsibility (CLI/web) — these functions just perform the operation,
they don't ask for confirmation themselves.

An imported file's location is always the project root: no subfolder
handling, the user no longer has to decide "where" (see the design
discussion — the location is "don't care" by choice).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from payload.core.batch_tables import BatchTable
from payload.core.cache import BuildCache
from payload.core.clusters import resolve_clusters
from payload.core.config import (
    GLOBAL_CONFIG_FILENAME,
    SIDECAR_SUFFIX,
    add_batch_table_source,
    create_batch_table,
    remove_batch_table_entry,
    remove_batch_table_source,
    remove_table_meta_entry,
    set_table_cluster,
    set_table_tags,
)
from payload.core.discovery import TableRef, all_table_refs, discover_for_history, resolve_table_ref
from payload.core.errors import (
    BatchTableError,
    EmptySourceError,
    InvalidImportError,
    SourceNotFoundError,
    TableAlreadyExistsError,
    TableNotFoundError,
)
from payload.core.history import HistoryStore
from payload.core.table_meta import resolve_table_meta


def _validate_filename(raw: str) -> str:
    """A plain filename, never a path — no separators, no '..', no
    empty/hidden name. The name comes from a web client (multipart
    upload) or the CLI: it's never trusted on its own."""
    if not raw or "/" in raw or "\\" in raw or raw in (".", "..") or raw.startswith("."):
        raise InvalidImportError(raw)
    return raw


def _name_taken(name: str, existing_sources: list[Path], existing_batch_tables: list[BatchTable]) -> bool:
    return name in {p.stem for p in existing_sources} or name in {bt.name for bt in existing_batch_tables}


def _validate_not_empty(filename: str, data: bytes) -> None:
    """A 0-byte file makes no sense as an import (almost always a
    wrong file or an interrupted upload): rejected BEFORE writing
    anything, same principle as the reader-side check."""
    if not data:
        raise EmptySourceError(filename)


def _validate_table_name(raw: str) -> str:
    """A table name for rename/clone: a plain stem, no path
    separators, no leading dot (would become a hidden file)."""
    if not raw or "/" in raw or "\\" in raw or raw in (".", "..") or raw.startswith("."):
        raise InvalidImportError(raw)
    return raw


def _rewrite_table_name_in_config(root: Path, old_name: str, new_name: str) -> None:
    """Rewrites `name = "<old>"` → `name = "<new>"` INSIDE
    [[table_meta]] and [[batch_table]] blocks only — the rest of
    table-tool.toml (comments, other sections) is preserved
    byte-for-byte."""
    path = root / GLOBAL_CONFIG_FILENAME
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_table_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_table_block = stripped in ("[[table_meta]]", "[[batch_table]]")
            continue
        if in_table_block:
            lines[i] = line.replace(f'name = "{old_name}"', f'name = "{new_name}"')
    path.write_text("".join(lines), encoding="utf-8")


def _existing_table_names(sources: list[Path], batch_tables: list[BatchTable]) -> set[str]:
    return {r.name for r in all_table_refs(sources, batch_tables)}


def rename_table(root: Path, old_name: str, new_name: str) -> dict:
    """Rename a table (single-file or batch) end to end: the source
    file on disk, its sidecar, the table's history (manifest +
    golden/head pointers) and the `name` in table-tool.toml. The
    history is migrated, not lost — snapshots, golden and
    tags/cluster follow the new name."""
    _validate_table_name(new_name)
    sources, batch_tables, _ = discover_for_history(root)
    ref = resolve_table_ref(sources, batch_tables, old_name)
    if ref is None:
        raise TableNotFoundError(old_name)
    if new_name == old_name:
        raise InvalidImportError("new name must differ from the current one")
    if new_name in _existing_table_names(sources, batch_tables):
        raise TableAlreadyExistsError(new_name)

    if not ref.is_batch:
        old_src = ref.source_paths[0]
        new_src = old_src.with_name(new_name + old_src.suffix)
        if new_src.exists():
            raise TableAlreadyExistsError(new_name)
        old_src.rename(new_src)
        old_sidecar = old_src.with_name(old_src.stem + SIDECAR_SUFFIX)
        new_sidecar = old_src.with_name(new_name + SIDECAR_SUFFIX)
        if old_sidecar.exists():
            old_sidecar.rename(new_sidecar)

    HistoryStore(root).rename_table(old_name, new_name)
    _rewrite_table_name_in_config(root, old_name, new_name)
    return {"from": old_name, "to": new_name, "is_batch": ref.is_batch}


def clone_table(root: Path, name: str, new_name: str) -> dict:
    """Duplicate a single-file table as a new one: source and sidecar
    copied, tags/cluster copied. History starts fresh (a clone has
    nothing committed yet)."""
    _validate_table_name(new_name)
    sources, batch_tables, base_config = discover_for_history(root)
    ref = resolve_table_ref(sources, batch_tables, name)
    if ref is None:
        raise TableNotFoundError(name)
    if new_name in _existing_table_names(sources, batch_tables):
        raise TableAlreadyExistsError(new_name)
    if ref.is_batch:
        # duplicate the [[batch_table]] entry: same members and
        # overrides (reader/writer/byte_order/pipeline), shared source
        # files, fresh history — symmetric with single-table clones
        b = ref.batch
        create_batch_table(
            root, new_name,
            [str(p.relative_to(root)) for p in b.source_paths],
            reader=b.reader, writer=b.writer, byte_order=b.byte_order,
            stages=b.stages,
        )
        return {"from": name, "to": new_name}

    old_src = ref.source_paths[0]
    new_src = old_src.with_name(new_name + old_src.suffix)
    if new_src.exists():
        raise TableAlreadyExistsError(new_name)
    shutil.copy2(old_src, new_src)
    old_sidecar = old_src.with_name(old_src.stem + SIDECAR_SUFFIX)
    new_sidecar = old_src.with_name(new_name + SIDECAR_SUFFIX)
    if old_sidecar.exists():
        shutil.copy2(old_sidecar, new_sidecar)

    clusters = resolve_clusters(root, base_config)
    metas = resolve_table_meta(root, base_config, clusters)
    meta = metas.get(name)
    if meta is not None:
        set_table_tags(root, new_name, meta.tags)
        if meta.cluster:
            set_table_cluster(root, new_name, meta.cluster)
    return {"from": name, "to": new_name}


@dataclass
class ImportResult:
    path: Path
    created: bool  # False if it overwrote the source of an already tracked table


def import_single_table(
    project_root: Path,
    data: bytes,
    filename: str,
    existing_sources: list[Path],
    existing_batch_tables: list[BatchTable],
    overwrite: bool = False,
) -> ImportResult:
    """Copies 'data' as a single-file table — new, or overwrites the
    source of one already tracked if overwrite=True and the name
    already exists (otherwise TableAlreadyExistsError). Doesn't
    require a reader for the extension to already be installed: import
    is just "put this file in the project", nothing here reads its
    content — a project can freely accumulate tables before installing
    (or writing) the plugin that will eventually build them. A format
    genuinely nothing can read only becomes a problem at build time
    (NoReaderFoundError there)."""
    filename = _validate_filename(filename)
    _validate_not_empty(filename, data)
    target = project_root / filename

    name = target.stem
    already_exists = _name_taken(name, existing_sources, existing_batch_tables)
    if already_exists and not overwrite:
        raise TableAlreadyExistsError(name)

    target.write_bytes(data)
    return ImportResult(path=target, created=not already_exists)


def import_new_batch_table(
    project_root: Path,
    files: dict[str, bytes],
    batch_name: str,
    existing_sources: list[Path],
    existing_batch_tables: list[BatchTable],
) -> BatchTable:
    """Creates a new [[batch_table]] from a group of files imported
    together — 'files' is {filename: bytes}; the concatenation order
    (see BATCH.md) follows the key order as given, no automatic
    reordering: the caller decides (e.g. natural order if the user
    dragged several files in together)."""
    if not files:
        raise BatchTableError(batch_name, "no file to import")
    if _name_taken(batch_name, existing_sources, existing_batch_tables):
        raise TableAlreadyExistsError(batch_name)

    filenames = [_validate_filename(f) for f in files]
    for filename, data in files.items():
        _validate_not_empty(filename, data)
        target = project_root / filename
        # A member filename that already exists on disk would otherwise
        # be silently overwritten by write_bytes below — e.g. it
        # collides with an already-tracked single-file table — and
        # folded into this new [[batch_table]], destroying the
        # original table with no confirmation. _name_taken() above
        # only guards the new batch's OWN name, not its members', so
        # this has to be checked separately, for every file, before
        # any write happens.
        if target.exists():
            raise TableAlreadyExistsError(target.stem)

    for filename, data in files.items():
        (project_root / filename).write_bytes(data)

    create_batch_table(project_root, batch_name, filenames)
    return BatchTable(name=batch_name, source_paths=[project_root / f for f in filenames])


@dataclass
class SkippedImport:
    filename: str
    reason: str


@dataclass
class BulkImportResult:
    imported: list[ImportResult]
    skipped: list[SkippedImport]


def import_many_single_tables(
    project_root: Path,
    files: dict[str, bytes],
    existing_sources: list[Path],
    existing_batch_tables: list[BatchTable],
    overwrite: bool = False,
) -> BulkImportResult:
    """Imports every file in 'files' as its OWN standalone table — the
    bulk counterpart of import_single_table, for a set of unrelated
    files that don't belong together (e.g. 300 files dropped together
    on the dashboard), as opposed to import_new_batch_table which
    builds ONE table out of several files. Unlike that function, a
    problem with one file (empty, unsafe filename, name collision)
    does NOT abort the rest: each file is validated and imported
    independently, and problems are collected and reported rather than
    silently skipped or allowed to block everything else — same
    'partial, but explicit' principle already used for writer fan-out
    failures (see FanOutWriteError in core/pipeline.py)."""
    imported: list[ImportResult] = []
    skipped: list[SkippedImport] = []
    taken = {p.stem for p in existing_sources} | {bt.name for bt in existing_batch_tables}

    for filename, data in files.items():
        try:
            filename = _validate_filename(filename)
            _validate_not_empty(filename, data)
            target = project_root / filename
        except (InvalidImportError, EmptySourceError) as e:
            skipped.append(SkippedImport(filename, e.message))
            continue

        name = target.stem
        already_exists = name in taken
        if already_exists and not overwrite:
            skipped.append(SkippedImport(filename, f"a table named '{name}' already exists"))
            continue

        target.write_bytes(data)
        imported.append(ImportResult(path=target, created=not already_exists))
        taken.add(name)

    return BulkImportResult(imported=imported, skipped=skipped)


def import_batch_member(
    project_root: Path,
    data: bytes,
    filename: str,
    batch: BatchTable,
) -> Path:
    """Adds one more file to an already declared [[batch_table]],
    always appended to the current order (for a different order, edit
    'sources' by hand — see BATCH.md)."""
    filename = _validate_filename(filename)
    _validate_not_empty(filename, data)
    if any(p.name == filename for p in batch.source_paths):
        raise TableAlreadyExistsError(f"{batch.name}/{filename}")
    target = project_root / filename

    target.write_bytes(data)
    add_batch_table_source(project_root, batch.name, filename)
    return target


@dataclass
class DeleteResult:
    removed_sources: list[Path]
    removed_outputs: list[Path]
    batch_entry_removed: bool


def delete_table(
    project_root: Path, ref: TableRef, output_dir: Path, cache: BuildCache,
) -> DeleteResult:
    """Deletes a table entirely: source(s), built output, and every
    cache entry that concerns it (doesn't call cache.save(): it's up
    to the caller to persist, same pattern as BuildCache.update()).
    NEVER touches the history: snapshots stay browsable, and for a
    single-file table also restorable (see
    HistoryStore.source_paths_for_snapshot). For a batch table, also
    removes the entire [[batch_table]] from table-tool.toml — restore
    from history is NOT supported for batch tables at this stage (see
    src/payload/docs/BATCH.md). Also drops the table's [[table_meta]]
    entry (cluster/tags), single-file or batch alike — a deleted table
    shouldn't leave orphaned metadata behind."""
    removed_sources = [p for p in ref.source_paths if p.is_file()]
    for p in removed_sources:
        p.unlink()

    removed_outputs = [p for p in output_dir.glob(f"{ref.name}.*") if p.is_file()] if output_dir.exists() else []
    for p in removed_outputs:
        p.unlink()

    cache.forget_table(ref.name)

    batch_entry_removed = remove_batch_table_entry(project_root, ref.name) if ref.is_batch else False
    remove_table_meta_entry(project_root, ref.name)
    return DeleteResult(removed_sources, removed_outputs, batch_entry_removed)


def delete_batch_member(
    project_root: Path, batch: BatchTable, filename: str, output_dir: Path, cache: BuildCache,
) -> DeleteResult:
    """Deletes ONE member file of a batch table. If none are left
    afterward, the entire [[batch_table]] is removed (same reasoning
    as delete_table, and the output must go too: the table no longer
    exists) — otherwise it tries to remove the matching literal path
    from 'sources' (only works if it was listed explicitly: a member
    matched by a glob pattern needs no config change, disappearing
    from disk is already enough since the glob won't find it anymore)."""
    target = next((p for p in batch.source_paths if p.name == filename), None)
    if target is None:
        raise SourceNotFoundError(Path(filename))

    removed_sources = []
    if target.is_file():
        target.unlink()
        removed_sources.append(target)

    cache.forget_table(batch.name)

    remaining = [p for p in batch.source_paths if p != target]
    if not remaining:
        batch_entry_removed = remove_batch_table_entry(project_root, batch.name)
        remove_table_meta_entry(project_root, batch.name)
        removed_outputs = [p for p in output_dir.glob(f"{batch.name}.*") if p.is_file()] if output_dir.exists() else []
        for p in removed_outputs:
            p.unlink()
        return DeleteResult(removed_sources, removed_outputs, batch_entry_removed)

    try:
        rel = target.relative_to(project_root).as_posix()
    except ValueError:
        rel = target.name
    remove_batch_table_source(project_root, batch.name, rel)

    return DeleteResult(removed_sources, [], False)
