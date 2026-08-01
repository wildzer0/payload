"""
Make-style incremental cache, but based on content hash, not mtime.

The key includes source + reader + writer + config, because the same
file with a different reader/writer/config produces different output.
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
    """Like compute_cache_key, but for the whole pipeline (stage_signature
    from PipelineSpec.cache_signature()) instead of a single reader/writer —
    changing even one stage in the middle invalidates the cache for the
    whole pipeline. Per-stage caching is a future extension, not here."""
    h = hashlib.sha256()
    h.update(source_bytes)
    h.update(stage_signature.encode())
    h.update(json.dumps(config, sort_keys=True, default=str).encode())
    return h.hexdigest()


def compute_pipeline_cache_key_multi(
    named_sources: list[tuple[str, bytes]], stage_signature: str, config: dict
) -> str:
    """Like compute_pipeline_cache_key, but for a batch table (N source
    files instead of 1). named_sources must already be sorted
    deterministically by the caller (same order used for parse_many).
    Each file goes into the hash as (name, length, bytes) instead of a
    concatenation into one big bytearray — streamed into the
    incremental sha256 object as above, with name/length before the
    bytes to avoid collisions like ["AB","CD"] == ["A","BCD"]."""
    h = hashlib.sha256()
    for name, data in named_sources:
        h.update(name.encode())
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
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
            # corrupted cache: not a fatal error, it's regenerated from
            # scratch. 'doctor' flags this case as WARN before it hits a build.
            logger.warning("Corrupted cache at %s, will be recreated (%s)", self.path, e)
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
            logger.debug("Cache miss for %s: no previous entry", table_key)
            return False
        if entry.input_hash != cache_key:
            logger.debug("Cache miss for %s: hash changed", table_key)
            return False
        missing = [p for p in entry.output_paths if not Path(p).exists()]
        if missing:
            logger.debug(
                "Cache miss for %s: output %s no longer present on disk",
                table_key, missing,
            )
            return False

        logger.debug("Cache hit for %s", table_key)
        return True

    def update(self, table_key: str, cache_key: str, output_path: Path | list[Path]) -> None:
        """output_path accepts either a single Path (a stage's
        checkpoint, always a single file) or a list[Path] (cache for a
        whole table, which produces several files with a fan-out)."""
        paths = [output_path] if isinstance(output_path, Path) else output_path
        with self._lock:
            self._entries[table_key] = CacheEntry(
                input_hash=cache_key, output_paths=[str(p) for p in paths]
            )

    def get_output_path(self, table_key: str) -> Path | None:
        """Returns the FIRST output_path registered for table_key, if
        any — used to resume execution from a stage checkpoint (always
        a single file) without having to re-run the earlier stages.
        Doesn't check freshness: call is_fresh() first."""
        with self._lock:
            entry = self._entries.get(table_key)
        return Path(entry.output_paths[0]) if entry and entry.output_paths else None

    def forget_table(self, table_name: str) -> int:
        """Removes every cache entry for this table — both the
        whole-build one (key = table name) and any intermediate stage
        checkpoints (key '<name>::stage<i>', see pipeline.py) — used by
        'pld rm', which must not leave orphaned cache for a deleted
        table. Returns how many entries were removed. Doesn't call
        save(): it's up to the caller to persist (same pattern as
        update())."""
        with self._lock:
            to_remove = [
                k for k in self._entries
                if k == table_name or k.startswith(f"{table_name}::stage")
            ]
            for k in to_remove:
                del self._entries[k]
        return len(to_remove)
