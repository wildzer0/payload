"""Package version, read from the installation metadata instead of
being duplicated by hand — pyproject.toml stays the single source of
truth, no risk of the two files drifting apart."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("payload")
except PackageNotFoundError:  # pragma: no cover
    # package not installed (e.g. run directly from source without
    # 'pip install -e .') — fallback so it doesn't break in development
    __version__ = "0.0.0+dev"
