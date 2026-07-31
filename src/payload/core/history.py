"""
Sistema di checkpoint leggero per le tabelle, ispirato a git ma
deliberatamente più semplice: nessun branch/merge/remote/staging-area,
solo uno storico lineare di snapshot per tabella.

Perché non "basta git": build/ è tipicamente gitignored (è un artefatto,
non un sorgente), quindi git da solo non lega mai insieme "sorgente
com'era" e "binario generato com'era" nello stesso momento. Questo
sistema cattura entrambi in un colpo solo, ad ogni commit.

Storage a blob content-addressed (stile git semplificato): ogni
contenuto unico va in .payload_history/objects/<sha256>, deduplicato
automaticamente — se una tabella non cambia tra due commit, non occupa
spazio doppio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from payload.core.errors import SnapshotNotFoundError

logger = logging.getLogger(__name__)

HISTORY_DIRNAME = ".payload_history"
OBJECTS_DIRNAME = "objects"
TABLES_DIRNAME = "tables"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class SnapshotMeta:
    id: int
    timestamp: str
    message: str
    source_blob: str
    output_blobs: dict = field(default_factory=dict)  # {filename: blob_hash}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMeta":
        return cls(**d)


class HistoryStore:
    def __init__(self, project_root: Path):
        self.root = project_root / HISTORY_DIRNAME
        self.objects_dir = self.root / OBJECTS_DIRNAME
        self.tables_dir = self.root / TABLES_DIRNAME

    def _ensure_dirs(self) -> None:
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.tables_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, table_name: str) -> Path:
        return self.tables_dir / f"{table_name}.json"

    def _load_manifest(self, table_name: str) -> list[SnapshotMeta]:
        path = self._manifest_path(table_name)
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        return [SnapshotMeta.from_dict(d) for d in raw]

    def _save_manifest(self, table_name: str, snapshots: list[SnapshotMeta]) -> None:
        self._ensure_dirs()
        path = self._manifest_path(table_name)
        path.write_text(json.dumps([s.to_dict() for s in snapshots], indent=2))

    def _blob_path(self, blob_hash: str) -> Path:
        # sharding stile git: objects/<primi 2 char>/<resto dell'hash>.
        # Una cartella piatta con migliaia di file degrada su alcuni
        # filesystem (specialmente su Windows/FAT più datati) — questo
        # tiene ogni sottocartella piccola indipendentemente dalla
        # dimensione del progetto.
        return self.objects_dir / blob_hash[:2] / blob_hash[2:]

    def _write_blob(self, data: bytes) -> str:
        self._ensure_dirs()
        h = _hash_bytes(data)
        blob_path = self._blob_path(h)
        if not blob_path.exists():  # dedup: contenuto già presente, non riscrive
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(data)
        return h

    def read_blob(self, blob_hash: str) -> bytes:
        blob_path = self._blob_path(blob_hash)
        if not blob_path.exists():
            raise SnapshotNotFoundError(blob_hash, reason="blob mancante nello storage (corrotto o cancellato a mano?)")
        return blob_path.read_bytes()

    def last_snapshot(self, table_name: str) -> SnapshotMeta | None:
        snapshots = self._load_manifest(table_name)
        return snapshots[-1] if snapshots else None

    def is_dirty(self, table_name: str, source_path: Path) -> bool:
        """True se il sorgente attuale differisce dall'ultimo snapshot,
        o se non esiste ancora nessuno snapshot per questa tabella."""
        last = self.last_snapshot(table_name)
        if last is None:
            return True
        current_hash = _hash_bytes(source_path.read_bytes())
        return current_hash != last.source_blob

    def commit(
        self, table_name: str, source_path: Path, output_paths: list[Path], message: str
    ) -> SnapshotMeta:
        snapshots = self._load_manifest(table_name)
        next_id = (snapshots[-1].id + 1) if snapshots else 1

        source_blob = self._write_blob(source_path.read_bytes())
        output_blobs = {}
        for out_path in output_paths:
            if out_path.exists() and out_path.is_file():
                output_blobs[out_path.name] = self._write_blob(out_path.read_bytes())

        snapshot = SnapshotMeta(
            id=next_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            message=message,
            source_blob=source_blob,
            output_blobs=output_blobs,
        )
        snapshots.append(snapshot)
        self._save_manifest(table_name, snapshots)
        logger.info("Snapshot #%d creato per %s (%d output allegati)", next_id, table_name, len(output_blobs))
        return snapshot

    def log(self, table_name: str) -> list[SnapshotMeta]:
        return self._load_manifest(table_name)

    def get_snapshot(self, table_name: str, snapshot_id: int) -> SnapshotMeta:
        for s in self._load_manifest(table_name):
            if s.id == snapshot_id:
                return s
        raise SnapshotNotFoundError(table_name, reason=f"nessuno snapshot #{snapshot_id}")

    def restore(
        self, table_name: str, snapshot_id: int, source_path: Path, output_dir: Path
    ) -> list[Path]:
        """Riporta sorgente e output generati allo stato dello snapshot.
        Ritorna la lista dei file effettivamente scritti."""
        snapshot = self.get_snapshot(table_name, snapshot_id)
        written = []

        source_path.write_bytes(self.read_blob(snapshot.source_blob))
        written.append(source_path)

        for filename, blob_hash in snapshot.output_blobs.items():
            out_path = output_dir / filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(self.read_blob(blob_hash))
            written.append(out_path)

        logger.info("Ripristinato %s allo snapshot #%d (%d file)", table_name, snapshot_id, len(written))
        return written

    def all_tracked_tables(self) -> list[str]:
        if not self.tables_dir.exists():
            return []
        return sorted(p.stem for p in self.tables_dir.glob("*.json"))
