"""
WatchSession: equivalente non-bloccante di 'pld watch' per il server
web. payload.watch.watch() (usato dalla CLI) blocca per sempre
(while True: time.sleep fino a KeyboardInterrupt) — inadatto qui, dove
il server deve continuare a rispondere ad altre richieste e deve poter
fermare l'osservazione da una chiamata API (niente Ctrl+C da un
browser). Si riusano solo le due primitive esportate da payload.watch:
_DebouncedTableHandler e watchdog.observers.Observer.

Il resto (_on_change) è un porting diretto della closure on_change già
dentro il comando 'watch' in cli.py — stessa chiamata a build(), stesso
cache.save() — solo trasmesso a N code (una per client SSE connesso)
invece di un singolo console.print."""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from watchdog.observers import Observer

from payload.core.cache import BuildCache
from payload.core.config import load_config
from payload.core.errors import PayloadError
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry
from payload.watch import DEFAULT_DEBOUNCE_SECONDS, _DebouncedTableHandler
from payload.web.paths import resolve

# Sentinella messa in ogni coda subscriber quando la sessione si ferma
# — permette al generatore SSE di chiudere lo stream con un frame
# finale invece di restare in attesa per sempre.
STOPPED = {"__control__": "stopped"}


class WatchSession:
    def __init__(
        self,
        root: Path,
        registry: PluginRegistry,
        output_dir: Path,
        writer_name: str | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ):
        self._root = root
        self._registry = registry
        self._output_dir = output_dir
        self._writer_name = writer_name
        self._debounce_seconds = debounce_seconds
        self._cache = BuildCache(resolve(root, load_config(root).defaults.cache_dir))
        self._lock = threading.Lock()  # guarda il ciclo di vita start/stop dell'observer
        self._observer: Observer | None = None
        self._sub_lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def is_running(self) -> bool:
        return self._observer is not None

    def start(self) -> None:
        """Idempotente: se già attiva, non fa nulla — più tab che
        chiamano start ricevono tutte lo stesso stream, invece di un
        errore (osservare lo stesso progetto da più punti è un caso
        d'uso legittimo, non un conflitto)."""
        with self._lock:
            if self._observer is not None:
                return
            known_ext = {ext for r in self._registry.readers.values() for ext in r.extensions}
            handler = _DebouncedTableHandler(
                known_ext, self._output_dir, self._on_change, debounce_seconds=self._debounce_seconds
            )
            observer = Observer()
            observer.schedule(handler, str(self._root), recursive=True)
            observer.start()
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            if self._observer is None:
                return
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        with self._sub_lock:
            targets = list(self._subscribers)
        for q in targets:
            q.put(STOPPED)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def _on_change(self, src: Path) -> None:
        try:
            per_table_config = load_config(self._root, source_path=src)
            out_paths, was_built = build(
                src, self._registry, per_table_config, self._output_dir,
                cache=self._cache, writer_name=self._writer_name,
            )
            self._cache.save()
            event = {
                "source": str(src), "status": "ok",
                "outputs": [str(p) for p in out_paths], "was_built": was_built,
            }
        except PayloadError as e:
            event = {"source": str(src), "status": "error", **e.to_dict()}

        with self._sub_lock:
            targets = list(self._subscribers)
        for q in targets:
            q.put(event)
