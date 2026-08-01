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

from dataclasses import dataclass
from pathlib import Path

from payload.core.batch_tables import BatchTable
from payload.core.cache import BuildCache
from payload.core.config import (
    add_batch_table_source,
    create_batch_table,
    remove_batch_table_entry,
    remove_batch_table_source,
)
from payload.core.discovery import TableRef
from payload.core.errors import (
    BatchTableError,
    EmptySourceError,
    InvalidImportError,
    SourceNotFoundError,
    TableAlreadyExistsError,
)
from payload.core.registry import PluginRegistry


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


@dataclass
class ImportResult:
    path: Path
    created: bool  # False if it overwrote the source of an already tracked table


def import_single_table(
    project_root: Path,
    registry: PluginRegistry,
    data: bytes,
    filename: str,
    existing_sources: list[Path],
    existing_batch_tables: list[BatchTable],
    overwrite: bool = False,
) -> ImportResult:
    """Copies 'data' as a single-file table — new, or overwrites the
    source of one already tracked if overwrite=True and the name
    already exists (otherwise TableAlreadyExistsError). Validates that
    at least one reader recognizes the extension BEFORE writing
    anything (NoReaderFoundError if none handles it) — a file no
    reader can read shouldn't even enter the project."""
    filename = _validate_filename(filename)
    _validate_not_empty(filename, data)
    target = project_root / filename
    registry.find_reader(target)

    name = target.stem
    already_exists = _name_taken(name, existing_sources, existing_batch_tables)
    if already_exists and not overwrite:
        raise TableAlreadyExistsError(name)

    target.write_bytes(data)
    return ImportResult(path=target, created=not already_exists)


def import_new_batch_table(
    project_root: Path,
    registry: PluginRegistry,
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
        registry.find_reader(project_root / filename)

    for filename, data in files.items():
        (project_root / filename).write_bytes(data)

    create_batch_table(project_root, batch_name, filenames)
    return BatchTable(name=batch_name, source_paths=[project_root / f for f in filenames])


def import_batch_member(
    project_root: Path,
    registry: PluginRegistry,
    data: bytes,
    filename: str,
    batch: BatchTable,
) -> Path:
    """Adds one more file to an already declared [[batch_table]],
    always appended to the current order (for a different order, edit
    'sources' by hand — see BATCH.md). Same reader validation as a
    single import."""
    filename = _validate_filename(filename)
    _validate_not_empty(filename, data)
    if any(p.name == filename for p in batch.source_paths):
        raise TableAlreadyExistsError(f"{batch.name}/{filename}")
    target = project_root / filename
    registry.find_reader(target)

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
    src/payload/docs/BATCH.md)."""
    removed_sources = [p for p in ref.source_paths if p.is_file()]
    for p in removed_sources:
        p.unlink()

    removed_outputs = [p for p in output_dir.glob(f"{ref.name}.*") if p.is_file()] if output_dir.exists() else []
    for p in removed_outputs:
        p.unlink()

    cache.forget_table(ref.name)

    batch_entry_removed = remove_batch_table_entry(project_root, ref.name) if ref.is_batch else False
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
