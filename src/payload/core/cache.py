"""
Cache incrementale stile Make ma basata su hash del contenuto, non su mtime.

La chiave include sorgente + reader + writer + config, perché stesso file
con reader/writer/config diversi produce output diverso.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILENAME = ".payload_cache.json"


@dataclass
class CacheEntry:
    input_hash: str
    output_paths: list[str]


def compute_cache_key(
    source_bytes: bytes, reader_name: str, writer_name: str, config: dict
) -> str:
    h = hashlib.sha256()
    h.update(source_bytes)
    h.update(reader_name.encode())
    h.update(writer_name.encode())
    h.update(json.dumps(config, sort_keys=True, default=str).encode())
    return h.hexdigest()


def compute_pipeline_cache_key(source_bytes: bytes, stage_signature: str, config: dict) -> str:
    """Come compute_cache_key, ma sull'intera pipeline (stage_signature
    da PipelineSpec.cache_signature()) invece di un solo reader/writer —
    cambiare anche un solo stage nel mezzo invalida la cache dell'intera
    pipeline. Cache per singolo stage è un'estensione futura, non qui."""
    h = hashlib.sha256()
    h.update(source_bytes)
    h.update(stage_signature.encode())
    h.update(json.dumps(config, sort_keys=True, default=str).encode())
    return h.hexdigest()


class BuildCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.path = cache_dir / CACHE_FILENAME
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            self._entries = {k: CacheEntry(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            # cache corrotta: non è un errore fatale, si rigenera da zero.
            # 'doctor' segnala questo caso come WARN prima che succeda in build.
            logger.warning("Cache corrotta in %s, verrà ricreata (%s)", self.path, e)
            self._entries = {}

    def save(self) -> None:
        with self._lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._entries.items()}
            self.path.write_text(json.dumps(data, indent=2))

    def is_fresh(self, table_key: str, cache_key: str) -> bool:
        with self._lock:
            entry = self._entries.get(table_key)

        if entry is None:
            logger.debug("Cache miss per %s: nessuna entry precedente", table_key)
            return False
        if entry.input_hash != cache_key:
            logger.debug("Cache miss per %s: hash cambiato", table_key)
            return False
        missing = [p for p in entry.output_paths if not Path(p).exists()]
        if missing:
            logger.debug(
                "Cache miss per %s: output %s non più presente/i su disco",
                table_key, missing,
            )
            return False

        logger.debug("Cache hit per %s", table_key)
        return True

    def update(self, table_key: str, cache_key: str, output_path: Path | list[Path]) -> None:
        """output_path accetta sia un singolo Path (checkpoint di uno
        stage, sempre un solo file) sia una list[Path] (cache di
        un'intera tabella, che con un fan-out produce più file)."""
        paths = [output_path] if isinstance(output_path, Path) else output_path
        with self._lock:
            self._entries[table_key] = CacheEntry(
                input_hash=cache_key, output_paths=[str(p) for p in paths]
            )

    def get_output_path(self, table_key: str) -> Path | None:
        """Ritorna il PRIMO output_path registrato per table_key, se
        esiste — usato per riprendere l'esecuzione da un checkpoint di
        stage (sempre un solo file) senza dover rieseguire gli stage
        precedenti. Non verifica freschezza: chiamare is_fresh() prima."""
        with self._lock:
            entry = self._entries.get(table_key)
        return Path(entry.output_paths[0]) if entry and entry.output_paths else None
