"""Shared helper for tests that exercise the real example plugins
(examples/plugins/*.py) — these are no longer part of the installed
payload package (see pyproject.toml), so tests load them the same way
a real project would: through core/local_plugins.py's file loader,
not a dotted import."""
from pathlib import Path

from payload.core.local_plugins import load_module_from_file

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def load_example_plugin(filename: str):
    """Loads examples/plugins/<filename> as a module and returns it —
    access classes/exports as module attributes, e.g.
    load_example_plugin('raw_text.py').RawTextReader."""
    return load_module_from_file(EXAMPLES_DIR / filename)
