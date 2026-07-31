"""
Golden file: test di regressione sull'output generato.

Salviamo i bytes completi (non solo hash): per tabelle embedded tipicamente
piccole, il diff leggibile a scopo debug vale più del peso extra in git.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

GOLDEN_SUFFIX = ".golden"


@dataclass
class GoldenResult:
    status: Literal["match", "mismatch", "missing"]
    golden_path: Path
    current: bytes | None = None
    expected: bytes | None = None


def golden_path_for(out_path: Path, golden_dir: Path) -> Path:
    return golden_dir / f"{out_path.name}{GOLDEN_SUFFIX}"


def check_golden(out_path: Path, golden_dir: Path) -> GoldenResult:
    gpath = golden_path_for(out_path, golden_dir)
    if not gpath.exists():
        logger.debug("Golden mancante per %s (%s)", out_path.name, gpath)
        return GoldenResult(status="missing", golden_path=gpath)

    current = out_path.read_bytes()
    expected = gpath.read_bytes()
    if current == expected:
        logger.debug("Golden match per %s", out_path.name)
        return GoldenResult(status="match", golden_path=gpath)
    logger.debug(
        "Golden mismatch per %s: %d bytes attuali vs %d bytes attesi",
        out_path.name, len(current), len(expected),
    )
    return GoldenResult(status="mismatch", golden_path=gpath, current=current, expected=expected)


def update_golden(out_path: Path, golden_dir: Path) -> Path:
    golden_dir.mkdir(parents=True, exist_ok=True)
    gpath = golden_path_for(out_path, golden_dir)
    gpath.write_bytes(out_path.read_bytes())
    logger.info("Golden aggiornato: %s", gpath)
    return gpath
