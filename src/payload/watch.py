"""
'pld watch': rebuild automatico al salvataggio, con debounce.

Punti chiave (vedi discussione precedente):
- Debounce: molti editor generano più eventi per un singolo salvataggio
  (write + rename, file di backup temporaneo). Un timer per-file raggruppa
  eventi ravvicinati prima di triggerare il build.
- La output dir è sempre esclusa dal watch, per evitare loop
  (build -> genera file -> trigger watch -> rebuild...).
- Un errore di build in watch mode non killa il processo: si logga e si
  continua a osservare, altrimenti l'esperienza iterativa si rompe.
- Riusa build() e la cache esistente: se il contenuto non è davvero
  cambiato (save senza modifiche), la cache fa comunque skip.
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
    """Filtra eventi per estensioni note, esclude output_dir, e raggruppa
    eventi ravvicinati sullo stesso file con un timer per-file."""

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
        if path.exists():  # potrebbe essere stato cancellato/rinominato nel frattempo
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
    """Blocca fino a Ctrl+C. on_change(path) viene invocato (in un thread
    del timer di debounce) per ogni file cambiato che rispetta il filtro."""

    def _safe_on_change(path: Path) -> None:
        try:
            on_change(path)
        except Exception as e:
            # un errore di build non deve mai killare il watch
            logger.error("Errore durante il rebuild di %s: %s", path.name, e)

    handler = _DebouncedTableHandler(known_extensions, output_dir, _safe_on_change, debounce_seconds)
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    logger.info("Watch avviato su %s (Ctrl+C per uscire)", root)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Watch interrotto")
    finally:
        observer.stop()
        observer.join()
