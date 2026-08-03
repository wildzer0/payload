"""
Lightweight checkpoint system for tables, inspired by git but
deliberately simpler: no branch/merge/remote/staging-area, just a
linear history of snapshots per table.

Why not "just use git": build/ is typically gitignored (it's an
artifact, not a source), so git alone never ties together "what the
source looked like" and "what the generated binary looked like" at the
same moment. This system captures both in one shot, on every commit.

Content-addressed blob storage (simplified git style): every unique
piece of content goes into .payload_history/objects/<sha256>,
automatically deduplicated — if a table doesn't change between two
commits, it doesn't take up double the space.
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
    """View of snap_source_blobs with the keys remapped to the CURRENT
    filenames when possible — needed for backward compatibility with
    manifests written before batch tables, where the key was a
    placeholder ('<source>', see SnapshotMeta.from_dict) because the
    filename wasn't saved. With exactly one file in both the snapshot
    and the current source_paths, the comparison is by VALUE, not by
    name — no ambiguity possible with a single element on both sides,
    and without this every table committed before this change would
    come out 'dirty'/non-restorable after the upgrade, despite being
    identical. For a batch table (>1 file) there's no legacy data to
    recover: it didn't exist before this feature."""
    if len(source_paths) == 1 and len(snap_source_blobs) == 1:
        return {source_paths[0].name: next(iter(snap_source_blobs.values()))}
    return snap_source_blobs


@dataclass
class SnapshotMeta:
    id: int
    timestamp: str
    message: str
    source_blobs: dict = field(default_factory=dict)  # {filename: blob_hash} — 1 entry for normal tables, N for a batch (see core/batch_tables.py)
    # {filename: dir relative to the project root, "" for the root itself}
    # — recorded on every commit, used to rebuild the path of a source
    # deleted from disk when doing a "cold" restore (see
    # source_paths_for_snapshot()). Snapshots created before this field
    # existed don't have it: source_paths_for_snapshot() assumes the
    # project root in that case — an honest degradation, not an error.
    source_dirs: dict = field(default_factory=dict)
    output_blobs: dict = field(default_factory=dict)  # {filename: blob_hash}
    reader: str | None = None  # reader resolved at commit time, informational only
    # effective byte_order at commit time — compared in is_dirty: a
    # byte_order change must be committable even when it doesn't alter
    # the output bytes (e.g. a reader with no multi-byte fields)
    byte_order: str | None = None
    writers: list = field(default_factory=list)  # writers inferred from the extension of the committed outputs
    pipeline_explicit: bool = False  # True if an explicit pipeline was in config at commit time (not just reader+writer)
    pipeline_description: str | None = None  # full stages, only if pipeline_explicit
    missing_outputs: list = field(default_factory=list)  # outputs expected by the pipeline but absent (e.g. partial fan-out)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMeta":
        d = dict(d)
        legacy_blob = d.pop("source_blob", None)
        if legacy_blob is not None and "source_blobs" not in d:
            # Manifest written before batch tables were introduced:
            # source_blob was a single string, with no filename (never
            # needed: restore() always writes to a path given by the
            # caller, not decided by the snapshot). This key is never
            # used for a lookup — is_dirty/restore for a single-file
            # table simply read the one value present.
            d["source_blobs"] = {"<source>": legacy_blob}
        return cls(**d)


@dataclass
class RestoreResult:
    written: list[Path]
    removed: list[Path]  # orphaned outputs (not in the restored snapshot) removed from disk


class HistoryStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root
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

    def rename_table(self, old_name: str, new_name: str) -> None:
        """Migrate a table's history after a rename: the per-table
        manifest file and the golden/head pointers. The blob store is
        content-addressed and shared, so it needs no move."""
        old_manifest = self._manifest_path(old_name)
        new_manifest = self._manifest_path(new_name)
        if old_manifest.exists() and not new_manifest.exists():
            old_manifest.rename(new_manifest)
        golden = self._load_golden_map()
        if old_name in golden:
            golden[new_name] = golden.pop(old_name)
            self._save_golden_map(golden)
        head = self._load_head_map()
        if old_name in head:
            head[new_name] = head.pop(old_name)
            self._save_head_map(head)

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

    def _relative_dir(self, p: Path) -> str:
        try:
            rel = p.parent.relative_to(self.project_root).as_posix()
        except ValueError:
            # outside the project root — shouldn't happen (source_paths
            # always come from discovery under root), but if it does we
            # degrade to the root instead of raising: worst case a cold
            # restore writes to the wrong place, fixable by hand, not a
            # crash.
            return ""
        return "" if rel == "." else rel

    def _blob_path(self, blob_hash: str) -> Path:
        # git-style sharding: objects/<first 2 chars>/<rest of the hash>.
        # A flat folder with thousands of files degrades on some
        # filesystems (especially older Windows/FAT) — this keeps every
        # subfolder small regardless of project size.
        return self.objects_dir / blob_hash[:2] / blob_hash[2:]

    def _write_blob(self, data: bytes) -> str:
        self._ensure_dirs()
        h = _hash_bytes(data)
        blob_path = self._blob_path(h)
        if not blob_path.exists():  # dedup: content already present, don't rewrite
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(data)
        return h

    def read_blob(self, blob_hash: str) -> bytes:
        blob_path = self._blob_path(blob_hash)
        if not blob_path.exists():
            raise SnapshotNotFoundError(blob_hash, reason="blob missing from storage (corrupted or deleted by hand?)")
        return blob_path.read_bytes()

    def tip_snapshot_id(self, table_name: str) -> int | None:
        """The most recent snapshot ever created for the table,
        regardless of where the head points — used to distinguish
        'tip of history' from 'the one currently restored' in the UI."""
        snapshots = self._load_manifest(table_name)
        return snapshots[-1].id if snapshots else None

    def head_snapshot_id(self, table_name: str) -> int | None:
        """The 'current' snapshot for the table: the one the last
        restore pointed to, or the tip of history if a restore was
        never done (or if the restore pointed to a snapshot that no
        longer exists). Reference used by is_dirty()/last_snapshot()
        instead of always assuming 'the last one committed' — after a
        restore to an earlier snapshot, that's the current one, until a
        new commit comes in."""
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

    def is_dirty(self, table_name: str, source_paths: list[Path], output_paths: list[Path] | None = None, byte_order: str | None = None) -> bool:
        """True if one of the current sources differs from the last
        snapshot, if no snapshot exists yet for this table, or if one
        of the current outputs (output_paths, if given) differs from
        what the last snapshot recorded — e.g. changing writer without
        touching the source produces a different output (including
        name and extension) that would otherwise go unnoticed: the
        source is identical, so comparing sources alone isn't enough to
        flag that there's something new to save. The comparison is by
        file NAME (dict-to-dict, same as already done for
        output_blobs): a file added/removed from a batch table between
        two commits is detected as 'dirty' even if the content of the
        other files hasn't changed, because the keys differ."""
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
        # the effective byte_order is part of the committed state: a
        # change is dirty even if the bytes came out identical
        if byte_order is not None and byte_order != last.byte_order:
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
        byte_order: str | None = None,
    ) -> SnapshotMeta:
        snapshots = self._load_manifest(table_name)
        next_id = (snapshots[-1].id + 1) if snapshots else 1

        source_blobs = {p.name: self._write_blob(p.read_bytes()) for p in source_paths}
        source_dirs = {p.name: self._relative_dir(p) for p in source_paths}
        output_blobs = {}
        for out_path in output_paths:
            if out_path.exists() and out_path.is_file():
                output_blobs[out_path.name] = self._write_blob(out_path.read_bytes())

        snapshot = SnapshotMeta(
            id=next_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            message=message,
            source_blobs=source_blobs,
            source_dirs=source_dirs,
            output_blobs=output_blobs,
            reader=reader,
            writers=list(writers) if writers else [],
            pipeline_explicit=pipeline_explicit,
            pipeline_description=pipeline_description,
            missing_outputs=list(missing_outputs) if missing_outputs else [],
            byte_order=byte_order,
        )
        snapshots.append(snapshot)
        self._save_manifest(table_name, snapshots)
        # a new commit is always the new tip AND the new "current":
        # any earlier restore to an older snapshot no longer makes
        # sense once there's a fresh commit on top.
        self._clear_head(table_name)
        logger.info("Snapshot #%d created for %s (%d outputs attached)", next_id, table_name, len(output_blobs))
        if snapshot.missing_outputs:
            logger.warning(
                "Snapshot #%d for %s: incomplete pipeline, missing %s",
                next_id, table_name, ", ".join(snapshot.missing_outputs),
            )
        return snapshot

    def log(self, table_name: str) -> list[SnapshotMeta]:
        return self._load_manifest(table_name)

    def get_snapshot(self, table_name: str, snapshot_id: int) -> SnapshotMeta:
        for s in self._load_manifest(table_name):
            if s.id == snapshot_id:
                return s
        raise SnapshotNotFoundError(table_name, reason=f"no snapshot #{snapshot_id}")

    def _load_golden_map(self) -> dict[str, int]:
        if not self._golden_path.exists():
            return {}
        try:
            return json.loads(self._golden_path.read_text())
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupted golden.json at %s, will be recreated", self._golden_path)
            return {}

    def _save_golden_map(self, mapping: dict[str, int]) -> None:
        self._ensure_dirs()
        self._golden_path.write_text(json.dumps(mapping, indent=2))

    def golden_snapshot_id(self, table_name: str) -> int | None:
        """No separate frozen file: golden is just a pointer to an
        already-recorded snapshot considered the reference — that
        snapshot's source and output ARE the golden."""
        return self._load_golden_map().get(table_name)

    def set_golden(self, table_name: str, snapshot_id: int) -> None:
        mapping = self._load_golden_map()
        mapping[table_name] = snapshot_id
        self._save_golden_map(mapping)
        logger.info("Golden for %s set to snapshot #%d", table_name, snapshot_id)

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
            logger.warning("Corrupted head.json at %s, will be recreated", self._head_path)
            return {}

    def _save_head_map(self, mapping: dict[str, int]) -> None:
        self._ensure_dirs()
        self._head_path.write_text(json.dumps(mapping, indent=2))

    def _clear_head(self, table_name: str) -> None:
        mapping = self._load_head_map()
        if table_name in mapping:
            del mapping[table_name]
            self._save_head_map(mapping)

    def source_paths_for_snapshot(self, table_name: str, snapshot_id: int) -> list[Path]:
        """Rebuilds the absolute paths of a snapshot's sources from the
        relative location recorded at commit time (source_dirs) — used
        to bring back to life a table deleted from disk, when live
        discovery can no longer provide the paths (the file isn't
        there, by definition). A snapshot created before source_dirs
        existed assumes the project root for every file — if the
        source lived elsewhere, the file ends up in the wrong place and
        needs to be moved by hand, an honest degradation instead of a
        crash."""
        snapshot = self.get_snapshot(table_name, snapshot_id)
        return [
            self.project_root / snapshot.source_dirs.get(name, "") / name
            for name in snapshot.source_blobs
        ]

    def restore(
        self, table_name: str, snapshot_id: int, source_paths: list[Path], output_dir: Path
    ) -> RestoreResult:
        """Brings sources and generated outputs back to the state of
        the snapshot and moves the "current" pointer (head) to that
        snapshot — no new snapshot is created: it's just a pointer
        move, 'git checkout <commit>' style. Later snapshots stay
        intact and browsable in the log, they just aren't the
        "current" one anymore until a new commit comes in (which
        overtakes them anyway, since ids always keep increasing).

        source_paths are the paths WHERE to write each source blob, by
        file name (same name-based matching already used for the
        outputs below) — normally the current ones (from live
        discovery), but they can also be the ones rebuilt by
        source_paths_for_snapshot() for a table no longer on disk: in
        that case the folders might no longer exist, so they're
        created as needed, same handling already in place for the
        outputs. A filename present in the snapshot but absent from
        source_paths (e.g. a file removed from the batch table after
        that commit) is simply skipped: there's no known path to write
        it to, it still stays browsable in the history.

        Also removes the outputs CURRENTLY on disk for this table that
        aren't part of the restored snapshot — e.g. a more recent
        snapshot built with a different writer left behind a file with
        another extension, which would otherwise stay orphaned on disk
        (and would make the table look 'changed' again right after the
        restore, since is_dirty() would see it as an extra output
        compared to the snapshot just restored). 'git checkout' style,
        not 'leave everything there'."""
        snapshot = self.get_snapshot(table_name, snapshot_id)
        written = []

        comparable_blobs = legacy_compatible_source_blobs(source_paths, snapshot.source_blobs)
        for source_path in source_paths:
            blob_hash = comparable_blobs.get(source_path.name)
            if blob_hash is None:
                logger.warning(
                    "%s: no source blob '%s' in snapshot #%d, skipping",
                    table_name, source_path.name, snapshot_id,
                )
                continue
            source_path.parent.mkdir(parents=True, exist_ok=True)
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
            "%s: current moved to snapshot #%d (%d files written, %d orphans removed)",
            table_name, snapshot_id, len(written), len(removed),
        )
        return RestoreResult(written=written, removed=removed)

    def all_tracked_tables(self) -> list[str]:
        if not self.tables_dir.exists():
            return []
        return sorted(p.stem for p in self.tables_dir.glob("*.json"))
