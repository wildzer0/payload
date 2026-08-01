"""
Batch tables: a logical table built from MULTIPLE source files of the
same format instead of just one (see src/payload/docs/BATCH.md),
declared explicitly in [[batch_table]] in table-tool.toml — never via
naming convention or folder structure, for consistency with the
approach already used for [pipeline.stages]/sidecar.

The shallow parsing (known field names, base types) lives in
core/config.py, exactly like pipeline_stages/pipeline_spec.py; this
module does the "deep" part: it expands the 'sources' patterns into
real filesystem paths, decides the concatenation order (see
_natural_sort_key) and validates that the result is actually
buildable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.errors import BatchTableError

if TYPE_CHECKING:
    from payload.core.config import PayloadConfig

_GLOB_CHARS = re.compile(r"[*?\[]")
_DIGIT_RUN = re.compile(r"(\d+)")


@dataclass
class BatchTable:
    name: str
    source_paths: list[Path]
    reader: str | None = None
    writer: str | None = None
    byte_order: str | None = None
    stages: list | None = None


def _natural_sort_key(path: Path) -> list:
    """Numeric comparison on the digit runs in the filename, so
    'ROW2.txt' comes before 'ROW10.txt' — unlike pure lexicographic
    order (where '1' < '10' < '2')."""
    parts = _DIGIT_RUN.split(path.name)
    return [int(p) if p.isdigit() else p for p in parts]


def _expand_sources(project_root: Path, batch_name: str, raw_sources: list[str]) -> list[Path]:
    """A literal entry (no glob metacharacter) keeps the position given
    in the list — full control for the user over concatenation order,
    useful because 'ROW10.txt' lexicographically precedes 'ROW2.txt'.
    A glob entry is expanded and sorted with _natural_sort_key."""
    resolved: list[Path] = []
    for raw in raw_sources:
        if _GLOB_CHARS.search(raw):
            matches = sorted((p for p in project_root.glob(raw) if p.is_file()), key=_natural_sort_key)
            resolved.extend(matches)
        else:
            path = project_root / raw
            if not path.is_file():
                raise BatchTableError(batch_name, f"source not found: '{raw}'")
            resolved.append(path)
    return resolved


def resolve_batch_tables(project_root: Path, config: "PayloadConfig") -> list[BatchTable]:
    """Expands config.batch_tables (raw list of dicts, validated only
    structurally by core/config.py) into BatchTable objects with
    source_paths already resolved/ordered on the filesystem. Raises
    BatchTableError for any entry that doesn't resolve to a valid set
    of files."""
    tables: list[BatchTable] = []
    seen_names: set[str] = set()

    for entry in config.batch_tables:
        name = entry["name"]
        if name in seen_names:
            raise BatchTableError(name, "duplicate name across multiple [[batch_table]]")
        seen_names.add(name)

        source_paths = _expand_sources(project_root, name, entry["sources"])
        if not source_paths:
            raise BatchTableError(name, "'sources' doesn't resolve to any file")

        seen_filenames: dict[str, Path] = {}
        for p in source_paths:
            if p.name in seen_filenames:
                raise BatchTableError(
                    name,
                    f"two different sources with the same filename '{p.name}' "
                    f"({seen_filenames[p.name]} and {p}) — filenames must be "
                    "unique within the same batch",
                )
            seen_filenames[p.name] = p

        tables.append(BatchTable(
            name=name,
            source_paths=source_paths,
            reader=entry.get("reader"),
            writer=entry.get("writer"),
            byte_order=entry.get("byte_order"),
            stages=entry.get("stages") or None,
        ))

    return tables


def effective_config(base_config: "PayloadConfig", batch: BatchTable) -> "PayloadConfig":
    """Overlays a [[batch_table]]'s inline overrides
    (reader/writer/byte_order/stages) on top of the global config — a
    batch table has no source_path to resolve a sidecar from, so the
    overrides live directly in the [[batch_table]] block. The result is
    a 'normal' PayloadConfig as far as
    resolve_pipeline_spec()/describe_table_build() are concerned: no
    duplicated resolution logic for the batch case."""
    defaults = replace(
        base_config.defaults,
        reader=batch.reader or base_config.defaults.reader,
        writer=batch.writer or base_config.defaults.writer,
        byte_order=batch.byte_order or base_config.defaults.byte_order,
    )
    return replace(
        base_config,
        defaults=defaults,
        pipeline_stages=batch.stages if batch.stages is not None else base_config.pipeline_stages,
    )
