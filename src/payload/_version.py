"""Versione del pacchetto, letta dai metadata di installazione invece
di essere duplicata a mano — pyproject.toml resta l'unica fonte di
verità, niente rischio di disallineamento tra i due file."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("payload")
except PackageNotFoundError:  # pragma: no cover
    # pacchetto non installato (es. eseguito direttamente da sorgente
    # senza 'pip install -e .') — fallback per non rompere in sviluppo
    __version__ = "0.0.0+dev"
