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
GOLDEN_FILENAME = "golden.json"
HEAD_FILENAME = "head.json"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def legacy_compatible_source_blobs(source_paths: list[Path], snap_source_blobs: dict) -> dict:
    """Vista di snap_source_blobs con le chiavi rimappate ai filename
    ATTUALI quando possibile — necessaria per la retrocompatibilità con
    manifest scritti prima delle tabelle batch, dove la chiave era un
    placeholder ('<source>', vedi SnapshotMeta.from_dict) perché il
    filename non veniva salvato. Con esattamente un file sia nello
    snapshot che nei source_paths correnti, il confronto è per VALORE,
    non per nome — nessuna ambiguità possibile con un solo elemento su
    entrambi i lati, e senza questo ogni tabella committata prima di
    questo cambio risulterebbe 'dirty'/non-restorabile dopo l'upgrade,
    pur essendo identica. Per una tabella batch (>1 file) non c'è dato
    legacy da recuperare: non esisteva prima di questa feature."""
    if len(source_paths) == 1 and len(snap_source_blobs) == 1:
        return {source_paths[0].name: next(iter(snap_source_blobs.values()))}
    return snap_source_blobs


@dataclass
class SnapshotMeta:
    id: int
    timestamp: str
    message: str
    source_blobs: dict = field(default_factory=dict)  # {filename: blob_hash} — 1 entry per tabelle normali, N per una batch (vedi core/batch_tables.py)
    output_blobs: dict = field(default_factory=dict)  # {filename: blob_hash}
    reader: str | None = None  # reader risolto al momento del commit, solo informativo
    writers: list = field(default_factory=list)  # writer dedotti dall'estensione degli output committati
    pipeline_explicit: bool = False  # True se al commit c'era una pipeline esplicita in config (non solo reader+writer)
    pipeline_description: str | None = None  # stage completi, solo se pipeline_explicit
    missing_outputs: list = field(default_factory=list)  # output attesi dalla pipeline ma assenti (es. fan-out parziale)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMeta":
        d = dict(d)
        legacy_blob = d.pop("source_blob", None)
        if legacy_blob is not None and "source_blobs" not in d:
            # Manifest scritto prima dell'introduzione delle tabelle
            # batch: source_blob era una stringa singola, senza filename
            # (mai serviva: restore() scrive sempre a un path dato dal
            # chiamante, non deciso dallo snapshot). La chiave qui non è
            # mai usata per un lookup — is_dirty/restore per una tabella
            # a un solo file leggono semplicemente l'unico valore presente.
            d["source_blobs"] = {"<source>": legacy_blob}
        return cls(**d)


@dataclass
class RestoreResult:
    written: list[Path]
    removed: list[Path]  # output orfani (non nello snapshot ripristinato) tolti dal disco


class HistoryStore:
    def __init__(self, project_root: Path):
        self.root = project_root / HISTORY_DIRNAME
        self.objects_dir = self.root / OBJECTS_DIRNAME
        self.tables_dir = self.root / TABLES_DIRNAME
        self._golden_path = self.root / GOLDEN_FILENAME
        self._head_path = self.root / HEAD_FILENAME

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

    def tip_snapshot_id(self, table_name: str) -> int | None:
        """L'ultimo snapshot mai creato per la tabella, indipendentemente
        da dove punta l'head — usato per distinguere 'punta della
        cronologia' da 'quello attualmente ripristinato' nella UI."""
        snapshots = self._load_manifest(table_name)
        return snapshots[-1].id if snapshots else None

    def head_snapshot_id(self, table_name: str) -> int | None:
        """Lo snapshot 'attuale' per la tabella: quello a cui punta
        l'ultimo restore, o la punta della cronologia se non è mai stato
        fatto un restore (o se il restore puntava a uno snapshot che non
        esiste più). Riferimento usato da is_dirty()/last_snapshot()
        invece di assumere sempre 'l'ultimo committato' — dopo un
        restore a uno snapshot precedente, l'attuale è quello, finché
        non arriva un nuovo commit."""
        snapshots = self._load_manifest(table_name)
        if not snapshots:
            return None
        head_id = self._load_head_map().get(table_name)
        if head_id is not None and any(s.id == head_id for s in snapshots):
            return head_id
        return snapshots[-1].id

    def last_snapshot(self, table_name: str) -> SnapshotMeta | None:
        head_id = self.head_snapshot_id(table_name)
        if head_id is None:
            return None
        return self.get_snapshot(table_name, head_id)

    def is_dirty(self, table_name: str, source_paths: list[Path], output_paths: list[Path] | None = None) -> bool:
        """True se uno dei sorgenti attuali differisce dall'ultimo
        snapshot, se non esiste ancora nessuno snapshot per questa
        tabella, o se uno degli output attuali (output_paths, se dato)
        differisce da quello che l'ultimo snapshot aveva registrato —
        es. cambiare writer senza toccare il sorgente produce un output
        diverso (nome ed estensione compresi) che altrimenti passerebbe
        inosservato: il sorgente è identico, quindi solo il confronto
        sui sorgenti non basta a segnalare che c'è qualcosa di nuovo da
        salvare. Il confronto è per NOME file (dict-a-dict, come già per
        output_blobs): un file aggiunto/rimosso da una tabella batch
        tra due commit viene rilevato come 'dirty' anche se il contenuto
        degli altri file non è cambiato, perché le chiavi differiscono."""
        last = self.last_snapshot(table_name)
        if last is None:
            return True
        current_sources = {p.name: _hash_bytes(p.read_bytes()) for p in source_paths}
        if current_sources != legacy_compatible_source_blobs(source_paths, last.source_blobs):
            return True
        if output_paths is not None:
            current_outputs = {p.name: _hash_bytes(p.read_bytes()) for p in output_paths if p.is_file()}
            if current_outputs != last.output_blobs:
                return True
        return False

    def commit(
        self,
        table_name: str,
        source_paths: list[Path],
        output_paths: list[Path],
        message: str,
        reader: str | None = None,
        writers: list[str] | None = None,
        pipeline_explicit: bool = False,
        pipeline_description: str | None = None,
        missing_outputs: list[str] | None = None,
    ) -> SnapshotMeta:
        snapshots = self._load_manifest(table_name)
        next_id = (snapshots[-1].id + 1) if snapshots else 1

        source_blobs = {p.name: self._write_blob(p.read_bytes()) for p in source_paths}
        output_blobs = {}
        for out_path in output_paths:
            if out_path.exists() and out_path.is_file():
                output_blobs[out_path.name] = self._write_blob(out_path.read_bytes())

        snapshot = SnapshotMeta(
            id=next_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            message=message,
            source_blobs=source_blobs,
            output_blobs=output_blobs,
            reader=reader,
            writers=list(writers) if writers else [],
            pipeline_explicit=pipeline_explicit,
            pipeline_description=pipeline_description,
            missing_outputs=list(missing_outputs) if missing_outputs else [],
        )
        snapshots.append(snapshot)
        self._save_manifest(table_name, snapshots)
        # un nuovo commit è sempre la nuova punta E il nuovo "attuale":
        # un eventuale restore precedente a uno snapshot più vecchio non
        # ha più senso una volta che c'è un commit fresco sopra.
        self._clear_head(table_name)
        logger.info("Snapshot #%d creato per %s (%d output allegati)", next_id, table_name, len(output_blobs))
        if snapshot.missing_outputs:
            logger.warning(
                "Snapshot #%d per %s: pipeline incompleta, mancano %s",
                next_id, table_name, ", ".join(snapshot.missing_outputs),
            )
        return snapshot

    def log(self, table_name: str) -> list[SnapshotMeta]:
        return self._load_manifest(table_name)

    def get_snapshot(self, table_name: str, snapshot_id: int) -> SnapshotMeta:
        for s in self._load_manifest(table_name):
            if s.id == snapshot_id:
                return s
        raise SnapshotNotFoundError(table_name, reason=f"nessuno snapshot #{snapshot_id}")

    def _load_golden_map(self) -> dict[str, int]:
        if not self._golden_path.exists():
            return {}
        try:
            return json.loads(self._golden_path.read_text())
        except (json.JSONDecodeError, TypeError):
            logger.warning("golden.json corrotto in %s, verrà ricreato", self._golden_path)
            return {}

    def _save_golden_map(self, mapping: dict[str, int]) -> None:
        self._ensure_dirs()
        self._golden_path.write_text(json.dumps(mapping, indent=2))

    def golden_snapshot_id(self, table_name: str) -> int | None:
        """Nessun file frozen separato: golden è solo un puntatore allo
        snapshot già registrato che si considera il riferimento —
        sorgente e output di quello snapshot SONO il golden."""
        return self._load_golden_map().get(table_name)

    def set_golden(self, table_name: str, snapshot_id: int) -> None:
        mapping = self._load_golden_map()
        mapping[table_name] = snapshot_id
        self._save_golden_map(mapping)
        logger.info("Golden per %s impostato allo snapshot #%d", table_name, snapshot_id)

    def clear_golden(self, table_name: str) -> bool:
        mapping = self._load_golden_map()
        if table_name not in mapping:
            return False
        del mapping[table_name]
        self._save_golden_map(mapping)
        return True

    def all_golden(self) -> dict[str, int]:
        return self._load_golden_map()

    def _load_head_map(self) -> dict[str, int]:
        if not self._head_path.exists():
            return {}
        try:
            return json.loads(self._head_path.read_text())
        except (json.JSONDecodeError, TypeError):
            logger.warning("head.json corrotto in %s, verrà ricreato", self._head_path)
            return {}

    def _save_head_map(self, mapping: dict[str, int]) -> None:
        self._ensure_dirs()
        self._head_path.write_text(json.dumps(mapping, indent=2))

    def _clear_head(self, table_name: str) -> None:
        mapping = self._load_head_map()
        if table_name in mapping:
            del mapping[table_name]
            self._save_head_map(mapping)

    def restore(
        self, table_name: str, snapshot_id: int, source_paths: list[Path], output_dir: Path
    ) -> RestoreResult:
        """Riporta sorgenti e output generati allo stato dello snapshot e
        sposta l'"attuale" (head) su quello snapshot — nessun nuovo
        snapshot viene creato: è solo uno spostamento di puntatore,
        stile 'git checkout <commit>'. Gli snapshot successivi restano
        intatti e consultabili nel log, semplicemente non sono più
        quelli "attuali" finché non arriva un nuovo commit (che li
        supera comunque, dato che gli id restano sempre crescenti).

        source_paths sono i path ATTUALI (dove scrivere ogni blob
        sorgente, per nome file — stesso abbinamento per nome già usato
        per gli output sotto). Un filename presente nello snapshot ma
        assente da source_paths (es. un file rimosso dalla tabella
        batch dopo quel commit) viene semplicemente saltato: non c'è un
        path noto dove scriverlo, resta comunque consultabile nello
        storico.

        Rimuove anche gli output ATTUALMENTE su disco per questa
        tabella che non fanno parte dello snapshot ripristinato — es.
        uno snapshot più recente costruito con un writer diverso ha
        lasciato un file con un'altra estensione, che altrimenti
        resterebbe orfano sul disco (e farebbe risultare la tabella
        'modificata' di nuovo subito dopo il restore, dato che
        is_dirty() lo vedrebbe come output in più rispetto allo
        snapshot appena ripristinato). Stile 'git checkout', non
        'lascia tutto lì'."""
        snapshot = self.get_snapshot(table_name, snapshot_id)
        written = []

        comparable_blobs = legacy_compatible_source_blobs(source_paths, snapshot.source_blobs)
        for source_path in source_paths:
            blob_hash = comparable_blobs.get(source_path.name)
            if blob_hash is None:
                logger.warning(
                    "%s: nessun blob sorgente '%s' nello snapshot #%d, salto",
                    table_name, source_path.name, snapshot_id,
                )
                continue
            source_path.write_bytes(self.read_blob(blob_hash))
            written.append(source_path)

        for filename, blob_hash in snapshot.output_blobs.items():
            out_path = output_dir / filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(self.read_blob(blob_hash))
            written.append(out_path)

        removed = []
        if output_dir.exists():
            for existing in output_dir.glob(f"{table_name}.*"):
                if existing.is_file() and existing.name not in snapshot.output_blobs:
                    existing.unlink()
                    removed.append(existing)

        mapping = self._load_head_map()
        mapping[table_name] = snapshot_id
        self._save_head_map(mapping)

        logger.info(
            "%s: attuale spostato allo snapshot #%d (%d file scritti, %d orfani rimossi)",
            table_name, snapshot_id, len(written), len(removed),
        )
        return RestoreResult(written=written, removed=removed)

    def all_tracked_tables(self) -> list[str]:
        if not self.tables_dir.exists():
            return []
        return sorted(p.stem for p in self.tables_dir.glob("*.json"))
