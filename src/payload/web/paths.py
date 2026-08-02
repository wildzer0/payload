"""Risolve un path relativo (da config, o da un parametro di richiesta
HTTP) rispetto alla root del progetto servito — MAI rispetto alla cwd
del processo server. Le due possono differire legittimamente (es.
'pld serve /altro/progetto' lanciato da una cartella diversa), a
differenza della CLI dove si assume implicitamente che l'utente lanci
i comandi dalla cartella del progetto (root == cwd)."""
from __future__ import annotations

from pathlib import Path

from payload.web.errors import InvalidRequestError


def resolve(root: Path, raw: str | Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else root / p


def resolve_contained(root: Path, raw: str | Path) -> Path:
    """Resolve a client-supplied path against the project root, refusing
    anything that would escape it (path traversal, symlink escapes:
    resolve() follows symlinks BEFORE the containment check, so a
    symlink inside the project pointing outside is caught too).

    Every /api/fs/* route goes through here — the plain resolve() is
    only safe because its inputs are table names / config keys, not
    free-form paths."""
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    try:
        p = p.resolve()
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - defensive, unresolvable path
        raise InvalidRequestError(f"invalid path '{raw}'")
    if p != root_resolved and root_resolved not in p.parents:
        raise InvalidRequestError(f"path '{raw}' is outside the project root")
    return p
