import pytest

from payload.core.errors import GoldenMissingError, SnapshotNotFoundError
from payload.core.golden import check_golden, clear_golden, golden_diff, set_golden
from payload.core.history import HistoryStore


def _commit(history, table, src, out, src_content, out_content):
    src.write_bytes(src_content)
    out.write_bytes(out_content)
    return history.commit(table, [src], [out], f"snapshot with out={out_content!r}")


def test_check_golden_missing_when_never_set(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    _commit(history, "t", src, out, b"src1", b"out1")

    status = check_golden(history, "t", [src], [out])

    assert status.status == "missing"
    assert status.golden_snapshot_id is None


def test_check_golden_match(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    status = check_golden(history, "t", [src], [out])

    assert status.status == "match"
    assert status.golden_snapshot_id == snap.id


def test_check_golden_mismatch_source_unchanged_output_different(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    out.write_bytes(b"out-different")  # source unchanged, only the output changes

    status = check_golden(history, "t", [src], [out])

    assert status.status == "mismatch"


def test_check_golden_stale_when_source_changed(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    src.write_bytes(b"src-different")  # the source itself changed

    status = check_golden(history, "t", [src], [out])

    assert status.status == "stale"


def test_set_golden_defaults_to_latest_snapshot(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    _commit(history, "t", src, out, b"src1", b"out1")
    snap2 = _commit(history, "t", src, out, b"src2", b"out2")

    golden_id = set_golden(history, "t")

    assert golden_id == snap2.id
    assert history.golden_snapshot_id("t") == snap2.id


def test_set_golden_explicit_older_snapshot(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap1 = _commit(history, "t", src, out, b"src1", b"out1")
    _commit(history, "t", src, out, b"src2", b"out2")

    set_golden(history, "t", snap1.id)

    assert history.golden_snapshot_id("t") == snap1.id


def test_set_golden_no_snapshot_raises(tmp_path):
    history = HistoryStore(tmp_path)
    with pytest.raises(SnapshotNotFoundError):
        set_golden(history, "t")


def test_set_golden_unknown_snapshot_id_raises(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    _commit(history, "t", src, out, b"src1", b"out1")

    with pytest.raises(SnapshotNotFoundError):
        set_golden(history, "t", 999)


def test_clear_golden(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    assert clear_golden(history, "t") is True
    assert history.golden_snapshot_id("t") is None
    assert clear_golden(history, "t") is False


def test_golden_diff_raises_when_missing(tmp_path):
    history = HistoryStore(tmp_path)
    with pytest.raises(GoldenMissingError):
        golden_diff(history, "t", [])


def test_golden_diff_empty_when_matching(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    assert golden_diff(history, "t", [out]) == {}


def test_golden_diff_reports_changed_bytes(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"\x00\x01\x02\x03")
    set_golden(history, "t", snap.id)

    out.write_bytes(b"\x00\xff\x02\x03")

    diff = golden_diff(history, "t", [out])

    assert list(diff.keys()) == ["t.bin"]
    assert diff["t.bin"][0]["offset"] == 0
    assert diff["t.bin"][0]["current"] == "00 ff 02 03"
    assert diff["t.bin"][0]["golden"] == "00 01 02 03"


def test_golden_diff_skips_missing_output_file(tmp_path):
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    missing_out = tmp_path / "nope.bin"

    assert golden_diff(history, "t", [missing_out]) == {}


def test_golden_diff_new_output_file_not_in_golden_snapshot(tmp_path):
    """An output not present in the golden snapshot (e.g. a new writer
    added later) is treated as different from 'nothing' (b''), so it
    shows up in the diff instead of being silently ignored."""
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    snap = _commit(history, "t", src, out, b"src1", b"out1")
    set_golden(history, "t", snap.id)

    new_out = tmp_path / "t.hex"
    new_out.write_bytes(b"new")

    diff = golden_diff(history, "t", [out, new_out])

    assert "t.hex" in diff
    assert "t.bin" not in diff


def test_check_golden_old_manifest_matches_by_value_not_filename(tmp_path):
    """Same regression as history.py: a golden snapshot written before
    batch tables doesn't know the real filename — the staleness
    comparison must still work by value."""
    history = HistoryStore(tmp_path)
    src = tmp_path / "t.raw"
    out = tmp_path / "t.bin"
    src.write_bytes(b"src unchanged")
    out.write_bytes(b"out1")
    history._ensure_dirs()
    blob_hash = history._write_blob(b"src unchanged")
    out_hash = history._write_blob(b"out1")
    history._manifest_path("t").write_text(
        '[{"id": 1, "timestamp": "2020-01-01T00:00:00", "message": "old", '
        f'"source_blob": "{blob_hash}", "output_blobs": {{"t.bin": "{out_hash}"}}}}]'
    )
    set_golden(history, "t", 1)

    assert check_golden(history, "t", [src], [out]).status == "match"

    src.write_bytes(b"src changed")
    assert check_golden(history, "t", [src], [out]).status == "stale"


def test_check_golden_batch_stale_when_any_member_source_changes(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    out = tmp_path / "rows.bin"
    row1.write_bytes(b"one")
    row2.write_bytes(b"two")
    out.write_bytes(b"out1")
    history = HistoryStore(tmp_path)
    snap = history.commit("rows", [row1, row2], [out], "v1")
    set_golden(history, "rows", snap.id)

    assert check_golden(history, "rows", [row1, row2], [out]).status == "match"

    row2.write_bytes(b"two-modified")
    assert check_golden(history, "rows", [row1, row2], [out]).status == "stale"
