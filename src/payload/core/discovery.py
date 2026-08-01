"""Discovery dei sorgenti tabella sotto una root. Condiviso da build-all
e dal sistema di history (status/commit devono vedere esattamente lo
stesso insieme di tabelle scoperto dal batch build, altrimenti 'pld
status' e 'pld build-all' potrebbero disaccordare su cosa esiste)."""
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
        # 'sensors/**' da solo matcha SOLO la cartella (comportamento
        # documentato ma controintuitivo di pathlib.glob), non i file al
        # suo interno — normalizziamo così l'uso intuitivo funziona.
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
    """Il nome tabella (filename stem) è l'identità usata per build
    output/golden/history — due sorgenti con lo stesso stem in cartelle
    diverse collidono silenziosamente su tutti e tre. Ritorna solo i
    gruppi con più di un file (dict vuoto se non ci sono duplicati)."""
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for p in sources:
        by_stem[p.stem].append(p)
    return {name: paths for name, paths in by_stem.items() if len(paths) > 1}


def check_no_batch_name_collisions(sources: list[Path], batch_tables: list[BatchTable]) -> None:
    """Un nome di [[batch_table]] che collide con lo stem di un file
    sorgente reale è la stessa ambiguità di due file con lo stesso
    stem — entrambi indicizzano build/golden/history per nome."""
    batch_names = {bt.name for bt in batch_tables}
    duplicates = {
        p.stem: [p, f"[[batch_table]] '{p.stem}'"]
        for p in sources if p.stem in batch_names
    }
    if duplicates:
        raise DuplicateTableNameError(duplicates)


def exclude_batch_members(sources: list[Path], batch_tables: list[BatchTable]) -> list[Path]:
    """Un file dichiarato come sorgente di una [[batch_table]] non deve
    comparire ANCHE come tabella a sé stante nella discovery normale —
    altrimenti (es. sources = ["ROW*.txt"] con .txt riconosciuta da un
    reader) ogni ROW1.txt/ROW2.txt verrebbe scoperto due volte: una
    volta come parte del batch 'rows', una volta come tabella
    standalone 'ROW1'/'ROW2', con build/output duplicati e confusi."""
    member_paths = {p for bt in batch_tables for p in bt.source_paths}
    return [s for s in sources if s not in member_paths]


def discover_for_history(root: Path) -> tuple[list[Path], list[BatchTable], "PayloadConfig"]:
    """Helper condiviso da CLI e web UI: stesso identico insieme di
    tabelle (file singoli + tabelle batch dichiarate in
    [[batch_table]]) che vedrebbe build-all, così 'pld status'/'pld
    commit' e la dashboard web non disaccordano mai su cosa esiste."""
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
    """Riferimento normalizzato a UNA tabella, indipendentemente dal
    fatto che sia un singolo file (source_paths ha 1 elemento,
    is_batch=False, batch=None) o una tabella batch (N elementi,
    is_batch=True, batch=il BatchTable con gli eventuali override
    reader/writer/byte_order/stages) — vedi src/payload/docs/BATCH.md."""

    name: str
    source_paths: list[Path]
    is_batch: bool
    batch: BatchTable | None = None


def resolve_table_ref(
    sources: list[Path], batch_tables: list[BatchTable], table_name: str
) -> "TableRef | None":
    """Sostituisce lo scan inline 'next((s for s in sources if s.stem
    == table_name), None)' duplicato in CLI/web — ora con un secondo
    ramo per le tabelle batch. Le tabelle batch sono controllate prima:
    il loro nome è dichiarato esplicitamente in config, non dedotto da
    un filename, quindi non c'è ambiguità da risolvere."""
    for bt in batch_tables:
        if bt.name == table_name:
            return TableRef(name=bt.name, source_paths=bt.source_paths, is_batch=True, batch=bt)
    for s in sources:
        if s.stem == table_name:
            return TableRef(name=table_name, source_paths=[s], is_batch=False)
    return None


def all_table_refs(sources: list[Path], batch_tables: list[BatchTable]) -> list[TableRef]:
    """Tutte le tabelle del progetto come TableRef, stesso ordine di
    'sources' seguito dalle tabelle batch — usato da build-all/status/
    commit/report/export, che devono iterare TUTTE le tabelle invece di
    risolverne una per nome."""
    refs = [TableRef(name=s.stem, source_paths=[s], is_batch=False) for s in sources]
    refs += [TableRef(name=bt.name, source_paths=bt.source_paths, is_batch=True, batch=bt) for bt in batch_tables]
    return refs
