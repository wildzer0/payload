"""
Golden: which historical snapshot of a table is the "correct"
regression reference. No longer a separate frozen file (the old
golden/*.golden) but a pointer to a snapshot already recorded in
HistoryStore — every commit already captures source+output together,
content-addressed and deduplicated, so "freezing" a golden is just
choosing WHICH snapshot to trust, zero extra file copies.

4-value status instead of the previous 3 (match/mismatch/missing):
- match: current source AND output both match the golden snapshot
- mismatch: source unchanged, but the current output differs — a real
  regression, the case golden exists for
- stale: the SOURCE changed after the golden snapshot — a mismatch
  here doesn't yet mean "regression", just "not re-verified yet"
  (previously indistinguishable from the case above)
- missing: no golden set for this table
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from payload.core.errors import GoldenMissingError, SnapshotNotFoundError
from payload.core.history import HistoryStore, legacy_compatible_source_blobs

logger = logging.getLogger(__name__)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class GoldenStatus:
    status: Literal["match", "mismatch", "stale", "missing"]
    golden_snapshot_id: int | None = None
    golden_message: str | None = None


def check_golden(
    history: HistoryStore, table_name: str, source_paths: list[Path], output_paths: list[Path]
) -> GoldenStatus:
    golden_id = history.golden_snapshot_id(table_name)
    if golden_id is None:
        return GoldenStatus(status="missing")

    snap = history.get_snapshot(table_name, golden_id)
    current_source_hashes = {p.name: _hash_bytes(p.read_bytes()) for p in source_paths}
    if current_source_hashes != legacy_compatible_source_blobs(source_paths, snap.source_blobs):
        return GoldenStatus(status="stale", golden_snapshot_id=golden_id, golden_message=snap.message)

    current_outputs = {p.name: _hash_bytes(p.read_bytes()) for p in output_paths if p.is_file()}
    if current_outputs != snap.output_blobs:
        return GoldenStatus(status="mismatch", golden_snapshot_id=golden_id, golden_message=snap.message)

    return GoldenStatus(status="match", golden_snapshot_id=golden_id, golden_message=snap.message)


def set_golden(history: HistoryStore, table_name: str, snapshot_id: int | None = None) -> int:
    """snapshot_id=None -> the table's latest snapshot. Returns the id
    that was actually set (useful when None gets resolved)."""
    if snapshot_id is None:
        last = history.last_snapshot(table_name)
        if last is None:
            raise SnapshotNotFoundError(table_name, reason="no snapshot exists yet, run 'pld commit' first")
        snapshot_id = last.id
    history.get_snapshot(table_name, snapshot_id)  # validates it exists, raises if missing
    history.set_golden(table_name, snapshot_id)
    return snapshot_id


def clear_golden(history: HistoryStore, table_name: str) -> bool:
    return history.clear_golden(table_name)


def golden_diff(
    history: HistoryStore, table_name: str, output_paths: list[Path]
) -> dict[str, list[dict]]:
    """{file name: [chunk, ...]} for every output that differs from
    the golden — only files that are actually different, an identical
    file doesn't show up in the result. Raises GoldenMissingError if
    no golden is set (nothing to diff against)."""
    golden_id = history.golden_snapshot_id(table_name)
    if golden_id is None:
        raise GoldenMissingError(table_name)
    snap = history.get_snapshot(table_name, golden_id)

    result: dict[str, list[dict]] = {}
    for p in output_paths:
        if not p.is_file():
            continue
        current = p.read_bytes()
        blob_hash = snap.output_blobs.get(p.name)
        expected = history.read_blob(blob_hash) if blob_hash else b""
        if current == expected:
            continue
        chunks = []
        max_len = max(len(current), len(expected))
        for i in range(0, max_len, 8):
            cur_chunk, exp_chunk = current[i:i + 8], expected[i:i + 8]
            if cur_chunk != exp_chunk:
                chunks.append({"offset": i, "current": cur_chunk.hex(" "), "golden": exp_chunk.hex(" ")})
        result[p.name] = chunks
    return result
