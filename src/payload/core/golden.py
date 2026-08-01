"""
Golden: quale snapshot storico di una tabella è il riferimento di
regressione "corretto". Non più un file frozen separato (vecchio
golden/*.golden) ma un puntatore a uno snapshot già registrato in
HistoryStore — ogni commit cattura già sorgente+output insieme,
content-addressed e deduplicato, quindi "congelare" un golden è solo
scegliere QUALE snapshot fidarsi, zero copie di file aggiuntive.

Stato a 4 valori invece dei 3 di prima (match/mismatch/missing):
- match: sorgente E output attuali coincidono con lo snapshot golden
- mismatch: sorgente invariato, ma l'output attuale è diverso — una
  vera regressione, il caso per cui golden esiste
- stale: il SORGENTE è cambiato dopo lo snapshot golden — un mismatch
  qui non significa ancora "regressione", solo "non ancora verificato
  di nuovo" (prima non era distinguibile dal caso sopra)
- missing: nessun golden impostato per questa tabella
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from payload.core.errors import GoldenMissingError, SnapshotNotFoundError
from payload.core.history import HistoryStore

logger = logging.getLogger(__name__)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class GoldenStatus:
    status: Literal["match", "mismatch", "stale", "missing"]
    golden_snapshot_id: int | None = None
    golden_message: str | None = None


def check_golden(
    history: HistoryStore, table_name: str, source_path: Path, output_paths: list[Path]
) -> GoldenStatus:
    golden_id = history.golden_snapshot_id(table_name)
    if golden_id is None:
        return GoldenStatus(status="missing")

    snap = history.get_snapshot(table_name, golden_id)
    current_source_hash = _hash_bytes(source_path.read_bytes())
    if current_source_hash != snap.source_blob:
        return GoldenStatus(status="stale", golden_snapshot_id=golden_id, golden_message=snap.message)

    current_outputs = {p.name: _hash_bytes(p.read_bytes()) for p in output_paths if p.is_file()}
    if current_outputs != snap.output_blobs:
        return GoldenStatus(status="mismatch", golden_snapshot_id=golden_id, golden_message=snap.message)

    return GoldenStatus(status="match", golden_snapshot_id=golden_id, golden_message=snap.message)


def set_golden(history: HistoryStore, table_name: str, snapshot_id: int | None = None) -> int:
    """snapshot_id=None -> l'ultimo snapshot della tabella. Ritorna
    l'id effettivamente impostato (utile quando None viene risolto)."""
    if snapshot_id is None:
        last = history.last_snapshot(table_name)
        if last is None:
            raise SnapshotNotFoundError(table_name, reason="nessuno snapshot esistente, esegui prima 'pld commit'")
        snapshot_id = last.id
    history.get_snapshot(table_name, snapshot_id)  # valida che esista, solleva se manca
    history.set_golden(table_name, snapshot_id)
    return snapshot_id


def clear_golden(history: HistoryStore, table_name: str) -> bool:
    return history.clear_golden(table_name)


def golden_diff(
    history: HistoryStore, table_name: str, output_paths: list[Path]
) -> dict[str, list[dict]]:
    """{nome file: [chunk, ...]} per ogni output che differisce dal
    golden — solo i file effettivamente diversi, un file identico non
    compare nel risultato. Solleva GoldenMissingError se non c'è un
    golden impostato (niente da cui calcolare un diff)."""
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
