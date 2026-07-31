"""
Logging strutturato su stderr, con livelli -v/-vv e tag automatico della
tabella corrente (utile in build paralleli, dove i log si interlacciano).
"""
from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

from payload.core.pipeline import current_table

LOGGER_ROOT = "payload"


class TableContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.table = current_table.get()
        return True


class TablePrefixFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        table = getattr(record, "table", None)
        prefix = f"[cyan][{table}][/] " if table else ""
        record.msg = f"{prefix}{record.getMessage()}"
        record.args = ()
        return super().format(record)


def setup_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)

    root = logging.getLogger(LOGGER_ROOT)
    root.setLevel(level)
    root.handlers.clear()

    handler = RichHandler(
        console=Console(stderr=True),
        show_time=verbosity >= 2,
        show_path=verbosity >= 2,
        markup=True,
        rich_tracebacks=True,
    )
    handler.addFilter(TableContextFilter())
    handler.setFormatter(TablePrefixFormatter("%(message)s"))
    root.addHandler(handler)
