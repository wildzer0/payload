"""Discovery dei sorgenti tabella sotto una root. Condiviso da build-all
e dal sistema di history (status/commit devono vedere esattamente lo
stesso insieme di tabelle scoperto dal batch build, altrimenti 'pld
status' e 'pld build-all' potrebbero disaccordare su cosa esiste)."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

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


def discover_for_history(root: Path) -> tuple[list[Path], "PayloadConfig"]:
    """Helper condiviso da CLI e web UI: stesso identico insieme di
    tabelle che vedrebbe build-all, così 'pld status'/'pld commit' e la
    dashboard web non disaccordano mai su cosa esiste."""
    registry = load_plugins(project_root=root)
    config = load_config(root)
    known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
    sources = discover_table_sources(root, known_ext, Path(config.defaults.output_dir))

    duplicates = find_duplicate_stems(sources)
    if duplicates:
        raise DuplicateTableNameError(duplicates)

    return sources, config
