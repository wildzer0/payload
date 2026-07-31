from pathlib import Path

import pytest

from payload.core.discovery import discover_table_sources
from payload.core.errors import SnapshotNotFoundError
from payload.core.history import HistoryStore


def test_never_committed_table_is_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    assert history.is_dirty("t", src) is True
    assert history.last_snapshot("t") is None


def test_commit_then_not_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", src, [], "primo")
    assert history.is_dirty("t", src) is False


def test_modify_after_commit_is_dirty_again(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", src, [], "primo")
    src.write_text("y")
    assert history.is_dirty("t", src) is True


def test_commit_ids_increment(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    s1 = history.commit("t", src, [], "v1")
    src.write_text("y")
    s2 = history.commit("t", src, [], "v2")
    assert s1.id == 1
    assert s2.id == 2


def test_log_returns_snapshots_in_order(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", src, [], "v1")
    src.write_text("y")
    history.commit("t", src, [], "v2")

    log = history.log("t")
    assert [s.message for s in log] == ["v1", "v2"]


def test_restore_brings_back_source_and_output(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("originale")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"output originale")

    history = HistoryStore(tmp_path)
    history.commit("t", src, [out], "v1")

    src.write_text("modificato")
    out.write_bytes(b"output modificato")

    written = history.restore("t", 1, src, out_dir)

    assert src.read_text() == "originale"
    assert out.read_bytes() == b"output originale"
    assert len(written) == 2


def test_restore_unknown_snapshot_raises(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", src, [], "v1")

    with pytest.raises(SnapshotNotFoundError):
        history.restore("t", 999, src, tmp_path / "build")


def test_identical_content_deduplicates_blobs(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("stesso contenuto")
    history = HistoryStore(tmp_path)

    s1 = history.commit("t", src, [], "v1")
    s2 = history.commit("t", src, [], "v2 nessuna modifica reale")

    assert s1.source_blob == s2.source_blob
    objects_dir = tmp_path / ".payload_history" / "objects"
    blob_files = [p for p in objects_dir.rglob("*") if p.is_file()]
    assert len(blob_files) == 1  # un solo blob su disco, non due


def test_all_tracked_tables_lists_committed_tables(tmp_path):
    history = HistoryStore(tmp_path)
    assert history.all_tracked_tables() == []

    src_a = tmp_path / "a.raw"
    src_a.write_text("a")
    src_b = tmp_path / "b.raw"
    src_b.write_text("b")
    history.commit("a", src_a, [], "v1")
    history.commit("b", src_b, [], "v1")

    assert history.all_tracked_tables() == ["a", "b"]


# --- discovery -------------------------------------------------------------

def test_discover_table_sources_excludes_output_dir(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "t1.bin").write_bytes(b"x")  # non deve comparire tra i sorgenti

    sources = discover_table_sources(tmp_path, {".raw"}, out_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_respects_filter_glob(tmp_path):
    (tmp_path / "sensors").mkdir()
    (tmp_path / "sensors" / "t1.raw").write_text("x")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "t2.raw").write_text("x")

    sources = discover_table_sources(tmp_path, {".raw"}, tmp_path / "build", filter_glob="sensors/**")
    assert [s.name for s in sources] == ["t1.raw"]
