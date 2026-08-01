"""
Tabelle batch: una tabella logica costruita da PIÙ file sorgente dello
stesso formato invece di uno solo (vedi src/payload/docs/BATCH.md),
dichiarata esplicitamente in [[batch_table]] di table-tool.toml — mai
per naming convention o struttura di cartelle, per coerenza con
l'approccio già usato per [pipeline.stages]/sidecar.

Il parsing shallow (nomi di campo noti, tipi base) vive in
core/config.py, esattamente come pipeline_stages/pipeline_spec.py;
questo modulo fa la parte "profonda": espande i pattern 'sources' in
path reali sul filesystem, decide l'ordine di concatenazione (vedi
_natural_sort_key) e valida che il risultato sia effettivamente
costruibile.
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
    """Confronto numerico sui blocchi di cifre nel filename, così
    'ROW2.txt' precede 'ROW10.txt' — a differenza dell'ordine
    lessicografico puro (dove '1' < '10' < '2')."""
    parts = _DIGIT_RUN.split(path.name)
    return [int(p) if p.isdigit() else p for p in parts]


def _expand_sources(project_root: Path, batch_name: str, raw_sources: list[str]) -> list[Path]:
    """Un elemento letterale (nessun metacarattere glob) mantiene la
    posizione data nella lista — controllo totale dell'utente
    sull'ordine di concatenazione, utile perché 'ROW10.txt' precede
    lessicograficamente 'ROW2.txt'. Un elemento glob viene espanso e
    ordinato con _natural_sort_key."""
    resolved: list[Path] = []
    for raw in raw_sources:
        if _GLOB_CHARS.search(raw):
            matches = sorted((p for p in project_root.glob(raw) if p.is_file()), key=_natural_sort_key)
            resolved.extend(matches)
        else:
            path = project_root / raw
            if not path.is_file():
                raise BatchTableError(batch_name, f"sorgente non trovato: '{raw}'")
            resolved.append(path)
    return resolved


def resolve_batch_tables(project_root: Path, config: "PayloadConfig") -> list[BatchTable]:
    """Espande config.batch_tables (lista grezza di dict, validata solo
    strutturalmente da core/config.py) in BatchTable con source_paths
    già risolti/ordinati sul filesystem. Solleva BatchTableError per
    qualunque entry che non si risolva in un insieme di file valido."""
    tables: list[BatchTable] = []
    seen_names: set[str] = set()

    for entry in config.batch_tables:
        name = entry["name"]
        if name in seen_names:
            raise BatchTableError(name, "nome duplicato tra più [[batch_table]]")
        seen_names.add(name)

        source_paths = _expand_sources(project_root, name, entry["sources"])
        if not source_paths:
            raise BatchTableError(name, "'sources' non risolve a nessun file")

        seen_filenames: dict[str, Path] = {}
        for p in source_paths:
            if p.name in seen_filenames:
                raise BatchTableError(
                    name,
                    f"due sorgenti diversi con lo stesso filename '{p.name}' "
                    f"({seen_filenames[p.name]} e {p}) — i filename devono essere "
                    "unici entro lo stesso batch",
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
    """Sovrappone gli override inline di una [[batch_table]]
    (reader/writer/byte_order/stages) sulla config globale — una
    tabella batch non ha un source_path da cui risolvere un sidecar,
    quindi gli override vivono direttamente nel blocco [[batch_table]].
    Il risultato è una PayloadConfig 'normale' agli occhi di
    resolve_pipeline_spec()/describe_table_build(): nessuna logica di
    risoluzione duplicata per il caso batch."""
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
