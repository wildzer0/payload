"""Risolve un path relativo (da config, o da un parametro di richiesta
HTTP) rispetto alla root del progetto servito — MAI rispetto alla cwd
del processo server. Le due possono differire legittimamente (es.
'pld serve /altro/progetto' lanciato da una cartella diversa), a
differenza della CLI dove si assume implicitamente che l'utente lanci
i comandi dalla cartella del progetto (root == cwd)."""
from __future__ import annotations

from pathlib import Path


def resolve(root: Path, raw: str | Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else root / p
