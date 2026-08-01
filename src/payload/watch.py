"""
'pld watch': automatic rebuild on save, with debounce.

Key points (see earlier discussion):
- Debounce: many editors generate several events for a single save
  (write + rename, temporary backup file). A per-file timer groups
  events that happen close together before triggering a build.
- The output dir is always excluded from the watch, to avoid loops
  (build -> generates a file -> triggers watch -> rebuild...).
- A build error in watch mode never kills the process: it's logged and
  watching continues, otherwise the iterative experience would break.
- Reuses build() and the existing cache: if the content hasn't really
  changed (save without modifications), the cache still skips it.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 0.3


class _DebouncedTableHandler(FileSystemEventHandler):
    """Filters events by known extensions, excludes output_dir, and
    groups events happening close together on the same file with a
    per-file timer."""

    def __init__(
        self,
        known_extensions: set[str],
        output_dir: Path,
        on_change: Callable[[Path], None],
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ):
        self._known_extensions = known_extensions
        self._output_dir = output_dir.resolve()
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    def _should_handle(self, path_str: str) -> Path | None:
        path = Path(path_str)
        if path.suffix not in self._known_extensions:
            return None
        try:
            if self._output_dir in path.resolve().parents:
                return None
        except OSError:
            pass
        return path

    def _schedule(self, path: Path) -> None:
        with self._lock:
            existing = self._timers.get(path)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._debounce_seconds, self._fire, args=(path,))
            self._timers[path] = timer
            timer.start()

    def _fire(self, path: Path) -> None:
        with self._lock:
            self._timers.pop(path, None)
        if path.exists():  # could have been deleted/renamed in the meantime
            self._on_change(path)

    def on_modified(self, event):
        if event.is_directory:
            return
        path = self._should_handle(event.src_path)
        if path:
            self._schedule(path)

    def on_created(self, event):
        self.on_modified(event)


def watch(
    root: Path,
    known_extensions: set[str],
    output_dir: Path,
    on_change: Callable[[Path], None],
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
) -> None:
    """Blocks until Ctrl+C. on_change(path) is invoked (on a debounce
    timer thread) for every changed file that passes the filter."""

    def _safe_on_change(path: Path) -> None:
        try:
            on_change(path)
        except Exception as e:
            # a build error must never kill the watch
            logger.error("Error while rebuilding %s: %s", path.name, e)

    handler = _DebouncedTableHandler(known_extensions, output_dir, _safe_on_change, debounce_seconds)
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    logger.info("Watch started on %s (Ctrl+C to exit)", root)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Watch interrupted")
    finally:
        observer.stop()
        observer.join()
