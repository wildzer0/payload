import json
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.discovery import discover_table_sources
from payload.core.errors import SnapshotNotFoundError
from payload.core.history import HistoryStore


def test_read_blob_missing_raises(tmp_path):
    history = HistoryStore(tmp_path)
    with pytest.raises(SnapshotNotFoundError):
        history.read_blob("hash_that_does_not_exist")


def test_never_committed_table_is_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    assert history.is_dirty("t", [src]) is True
    assert history.last_snapshot("t") is None


def test_commit_then_not_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "first")
    assert history.is_dirty("t", [src]) is False


def test_modify_after_commit_is_dirty_again(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "first")
    src.write_text("y")
    assert history.is_dirty("t", [src]) is True


def test_dirty_when_output_changed_but_source_did_not(tmp_path):
    """Regression: changing writer (or just the output in general, e.g.
    a different writer -> different name/extension) without touching
    the source used to be seen as 'unchanged' because is_dirty() only
    looked at the source — the commit had no way to notice there was a
    new output to save."""
    src = tmp_path / "t.raw"
    src.write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out_bin = out_dir / "t.bin"
    out_bin.write_bytes(b"output bin")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out_bin], "v1 with bin writer")

    assert history.is_dirty("t", [src], [out_bin]) is False

    # same source, different writer: different output file (name and
    # extension included), whether the previous 't.bin' remains or not
    # doesn't matter, what matters is what's NOW in output_paths
    out_hex = out_dir / "t.hex"
    out_hex.write_bytes(b"output hex, different")

    assert history.is_dirty("t", [src], [out_hex]) is True


def test_dirty_when_output_content_changed_same_filename(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"before")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")
    assert history.is_dirty("t", [src], [out]) is False

    out.write_bytes(b"after, different content")
    assert history.is_dirty("t", [src], [out]) is True


def test_commit_ids_increment(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    s1 = history.commit("t", [src], [], "v1")
    src.write_text("y")
    s2 = history.commit("t", [src], [], "v2")
    assert s1.id == 1
    assert s2.id == 2


# --- golden pointer ---


def test_golden_snapshot_id_none_by_default(tmp_path):
    history = HistoryStore(tmp_path)
    assert history.golden_snapshot_id("t") is None


def test_set_and_get_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)
    assert history.golden_snapshot_id("t") == 3


def test_set_golden_overwrites_previous(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)
    history.set_golden("t", 7)
    assert history.golden_snapshot_id("t") == 7


def test_clear_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)

    assert history.clear_golden("t") is True
    assert history.golden_snapshot_id("t") is None
    assert history.clear_golden("t") is False  # idempotent


def test_all_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t1", 1)
    history.set_golden("t2", 4)
    assert history.all_golden() == {"t1": 1, "t2": 4}


def test_golden_map_survives_reload(tmp_path):
    HistoryStore(tmp_path).set_golden("t", 5)
    assert HistoryStore(tmp_path).golden_snapshot_id("t") == 5


def test_golden_map_corrupted_recreated(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 1)
    history._golden_path.write_text("{not json")

    assert history.golden_snapshot_id("t") is None
    history.set_golden("t2", 2)
    assert HistoryStore(tmp_path).all_golden() == {"t2": 2}


def test_head_map_corrupted_falls_back_to_tip(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    history._head_path.write_text("{not json")

    assert history.head_snapshot_id("t") == 1
    assert history.last_snapshot("t").message == "v1"


def test_commit_after_restore_clears_head_override(tmp_path):
    """A commit made after restoring to an earlier snapshot becomes the
    new tip AND the new 'current': the override left by the restore no
    longer makes sense once there's a fresh commit on top, otherwise
    the new commit would stay invisible to
    last_snapshot()/is_dirty()."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")

    src.write_text("v2")
    history.commit("t", [src], [], "v2")

    history.restore("t", 1, [src], tmp_path / "build")
    assert history.head_snapshot_id("t") == 1

    src.write_text("v3")
    snap = history.commit("t", [src], [], "v3")

    assert snap.id == 3
    assert history.head_snapshot_id("t") == 3
    assert history.tip_snapshot_id("t") == 3
    assert history.last_snapshot("t").message == "v3"


def test_restore_does_not_create_new_snapshot(tmp_path):
    """The 'pointer only' redesign: restore must never add an entry to
    the history, unlike the old 'git revert'-style behavior."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    src.write_text("v2")
    history.commit("t", [src], [], "v2")

    history.restore("t", 1, [src], tmp_path / "build")
    history.restore("t", 2, [src], tmp_path / "build")

    assert len(history.log("t")) == 2
    assert history.head_snapshot_id("t") == 2


def test_commit_records_reader_and_writers(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", [src], [], "v1", reader="raw_text", writers=["bin", "header"])

    assert snap.reader == "raw_text"
    assert snap.writers == ["bin", "header"]
    reloaded = HistoryStore(tmp_path).get_snapshot("t", 1)
    assert reloaded.reader == "raw_text"
    assert reloaded.writers == ["bin", "header"]


def test_old_manifest_without_reader_writer_fields_still_loads(tmp_path):
    """Backward compatibility: a manifest written before the
    reader/writers fields were added must keep loading, with those
    fields at their default values."""
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    manifest_path = history._manifest_path("t")
    manifest_path.write_text(json.dumps([
        {"id": 1, "timestamp": "2020-01-01T00:00:00", "message": "old", "source_blob": "abc", "output_blobs": {}}
    ]))

    snap = history.get_snapshot("t", 1)
    assert snap.reader is None
    assert snap.writers == []


def test_old_manifest_is_dirty_still_works_by_value_not_filename(tmp_path):
    """Regression: a snapshot written before batch tables doesn't know
    the real filename (only the hash, under a placeholder key) —
    is_dirty must not compare the dict KEYS in this case (it would
    always fail, 'placeholder' != 't.raw'), only the value."""
    src = tmp_path / "t.raw"
    src.write_bytes(b"unchanged content")
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    history._manifest_path("t").write_text(json.dumps([{
        "id": 1, "timestamp": "2020-01-01T00:00:00", "message": "old",
        "source_blob": history._write_blob(b"unchanged content"), "output_blobs": {},
    }]))

    assert history.is_dirty("t", [src]) is False

    src.write_bytes(b"changed content")
    assert history.is_dirty("t", [src]) is True


def test_old_manifest_restore_still_writes_the_source_by_value(tmp_path):
    src = tmp_path / "t.raw"
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    blob_hash = history._write_blob(b"original content")
    history._manifest_path("t").write_text(json.dumps([{
        "id": 1, "timestamp": "2020-01-01T00:00:00", "message": "old",
        "source_blob": blob_hash, "output_blobs": {},
    }]))

    src.write_bytes(b"modified")
    result = history.restore("t", 1, [src], tmp_path / "build")

    assert src.read_bytes() == b"original content"
    assert result.written == [src]


def test_log_returns_snapshots_in_order(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    src.write_text("y")
    history.commit("t", [src], [], "v2")

    log = history.log("t")
    assert [s.message for s in log] == ["v1", "v2"]


def test_restore_brings_back_source_and_output(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("original")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"original output")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")

    src.write_text("modified")
    out.write_bytes(b"modified output")

    result = history.restore("t", 1, [src], out_dir)

    assert src.read_text() == "original"
    assert out.read_bytes() == b"original output"
    assert len(result.written) == 2
    assert result.removed == []


def test_restore_leaves_table_clean_not_dirty(tmp_path):
    """Regression: is_dirty() must compare the just-restored state
    with the 'current' (head) snapshot, not with the latest ever
    committed (the tip), otherwise the table would come out 'changed'
    right after a successful restore."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"out-v1")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")

    src.write_text("v2")
    out.write_bytes(b"out-v2")
    history.commit("t", [src], [out], "v2")

    history.restore("t", 1, [src], out_dir)

    assert history.is_dirty("t", [src]) is False
    log = history.log("t")
    # restore does NOT create a new snapshot: it only moves the
    # current pointer backward, the history stays additive and unchanged.
    assert len(log) == 2
    assert history.head_snapshot_id("t") == 1
    assert history.tip_snapshot_id("t") == 2
    assert history.last_snapshot("t").message == "v1"
    assert src.read_text() == "v1"


def test_restore_removes_orphaned_output_from_a_different_writer(tmp_path):
    """Regression found by a user: if the writer changes between two
    snapshots (e.g. bin -> header), the later writer's output stays
    physically on disk even after restoring to the earlier snapshot —
    unlike git, which removes files not present in the target commit
    on checkout. Without cleanup, the table would come out 'changed'
    again right after the restore (the orphaned output isn't part of
    the new snapshot the restore itself just made current)."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out_bin = out_dir / "t.bin"
    out_bin.write_bytes(b"out-bin")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out_bin], "v1 with bin writer")

    src.write_text("v2")
    out_header = out_dir / "t.h"
    out_header.write_bytes(b"out-header")
    history.commit("t", [src], [out_header], "v2 with header writer")

    assert out_bin.exists() and out_header.exists()  # both present before the restore

    result = history.restore("t", 1, [src], out_dir)

    assert src.read_text() == "v1"
    assert out_bin.exists()
    assert not out_header.exists()  # orphan, wasn't part of snapshot #1
    assert result.removed == [out_header]

    current_outputs = list(out_dir.glob("t.*"))
    assert history.is_dirty("t", [src], current_outputs) is False


def test_restore_skips_filename_absent_from_the_snapshot(tmp_path):
    """A batch table that had a member file ADDED after a commit: that
    file has no blob in that snapshot (it didn't exist yet) — restore
    skips it with a warning instead of raising, still restoring the
    other batch files normally."""
    row1 = tmp_path / "ROW1.txt"
    row3 = tmp_path / "ROW3.txt"
    row1.write_text("one")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1], [], "v1, ROW1 only")

    row1.write_text("modified")
    row3.write_text("new file, not in snapshot #1")
    result = history.restore("rows", 1, [row1, row3], tmp_path / "build")

    assert row1.read_text() == "one"
    assert row3.read_text() == "new file, not in snapshot #1"  # untouched
    assert result.written == [row1]


def test_restore_unknown_snapshot_raises(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")

    with pytest.raises(SnapshotNotFoundError):
        history.restore("t", 999, [src], tmp_path / "build")


def test_identical_content_deduplicates_blobs(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("same content")
    history = HistoryStore(tmp_path)

    s1 = history.commit("t", [src], [], "v1")
    s2 = history.commit("t", [src], [], "v2, no real change")

    assert s1.source_blobs == s2.source_blobs
    objects_dir = tmp_path / ".payload_history" / "objects"
    blob_files = [p for p in objects_dir.rglob("*") if p.is_file()]
    assert len(blob_files) == 1  # a single blob on disk, not two


# --- batch tables (source_paths with N > 1 elements) ------------------------


def test_batch_commit_stores_one_blob_per_source_filename(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("one")
    row2.write_text("two")
    history = HistoryStore(tmp_path)

    snap = history.commit("rows", [row1, row2], [], "v1")

    assert snap.source_blobs.keys() == {"ROW1.txt", "ROW2.txt"}


def test_batch_not_dirty_after_commit_dirty_after_any_member_changes(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("one")
    row2.write_text("two")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    assert history.is_dirty("rows", [row1, row2]) is False

    row2.write_text("two-modified")
    assert history.is_dirty("rows", [row1, row2]) is True


def test_batch_dirty_when_a_member_file_is_added_or_removed(tmp_path):
    """Emergent bonus of the dict-to-dict comparison: a file added or
    removed from the batch between two commits is 'dirty' even if the
    content of the other files hasn't changed, because the dict's KEYS
    differ."""
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row3 = tmp_path / "ROW3.txt"
    row1.write_text("one")
    row2.write_text("two")
    row3.write_text("three")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    assert history.is_dirty("rows", [row1, row2, row3]) is True
    assert history.is_dirty("rows", [row1]) is True


def test_batch_restore_writes_back_every_member_file(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("one")
    row2.write_text("two")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    row1.write_text("changed")
    row2.write_text("this too")

    result = history.restore("rows", 1, [row1, row2], tmp_path / "build")

    assert row1.read_text() == "one"
    assert row2.read_text() == "two"
    assert set(result.written) == {row1, row2}


def test_all_tracked_tables_lists_committed_tables(tmp_path):
    history = HistoryStore(tmp_path)
    assert history.all_tracked_tables() == []

    src_a = tmp_path / "a.raw"
    src_a.write_text("a")
    src_b = tmp_path / "b.raw"
    src_b.write_text("b")
    history.commit("a", [src_a], [], "v1")
    history.commit("b", [src_b], [], "v1")

    assert history.all_tracked_tables() == ["a", "b"]


# --- discovery -------------------------------------------------------------

def test_discover_table_sources_excludes_output_dir(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "t1.bin").write_bytes(b"x")  # must not show up among sources

    sources = discover_table_sources(tmp_path, out_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_excludes_matching_extension_inside_output_dir(tmp_path):
    """A file INSIDE output_dir (not just a different one, like in the
    test above) must still be excluded — otherwise a build that
    regenerates a .raw inside build/ (edge case but possible) would
    get picked back up as a source."""
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "regenerated.raw").write_text("x")

    sources = discover_table_sources(tmp_path, out_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_excludes_cache_dir(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    cache_dir = tmp_path / "my_cache"
    cache_dir.mkdir()
    (cache_dir / "entry.raw").write_text("x")

    sources = discover_table_sources(tmp_path, tmp_path / "build", cache_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_ignores_no_reader_for_extension(tmp_path):
    """Regression: discovery must NOT require an installed reader for
    the file's extension — a fresh project starts with zero readers
    (see the no-bundled-plugins refactor), gating discovery on one
    made every imported file invisible until a matching plugin was
    installed, which broke 'pld status'/the dashboard for any project
    that hadn't installed a reader yet (even pld init's own
    example_table.raw)."""
    (tmp_path / "mystery.unknownext").write_text("x")

    sources = discover_table_sources(tmp_path, tmp_path / "build")
    assert [s.name for s in sources] == ["mystery.unknownext"]


def test_discover_table_sources_excludes_global_config_and_sidecars(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    (tmp_path / "table-tool.toml").write_text("")
    (tmp_path / "t1.config.toml").write_text("")

    sources = discover_table_sources(tmp_path, tmp_path / "build")
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_excludes_hidden_files_and_dirs(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    (tmp_path / ".hidden.raw").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")

    sources = discover_table_sources(tmp_path, tmp_path / "build")
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_excludes_plugins_dir(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "not_a_table.raw").write_text("x")

    sources = discover_table_sources(tmp_path, tmp_path / "build")
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_output_dir_relative_to_root_not_cwd(tmp_path, monkeypatch):
    """A relative output_dir must resolve against 'root', never the
    calling process's cwd — 'pld serve /other/project' (or 'pld status
    --root ...') from a different folder is a legitimate case. A bare
    Path(output_dir) resolved against cwd would silently exclude
    nothing, letting the build output leak back in as a fake table."""
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "t1.bin").write_bytes(b"x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    sources = discover_table_sources(tmp_path, Path("build"))
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_respects_filter_glob(tmp_path):
    (tmp_path / "sensors").mkdir()
    (tmp_path / "sensors" / "t1.raw").write_text("x")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "t2.raw").write_text("x")

    sources = discover_table_sources(tmp_path, tmp_path / "build", filter_glob="sensors/**")
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_tolerates_unresolvable_output_dir(tmp_path):
    """If output_dir.resolve() fails (e.g. permissions, unusual
    filesystems), discovery must not crash — it degrades to using the
    unresolved path instead of raising."""
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    real_resolve = Path.resolve

    def fake_resolve(self, *a, **kw):
        if self == out_dir:
            raise OSError("simulated")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", fake_resolve):
        sources = discover_table_sources(tmp_path, out_dir)

    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_tolerates_unresolvable_source(tmp_path):
    """If a candidate's resolve() fails, it must still be included
    among the sources instead of being silently lost (fail-safe:
    better a false positive than a table disappearing from
    discovery)."""
    src = tmp_path / "t1.raw"
    src.write_text("x")
    real_resolve = Path.resolve

    def fake_resolve(self, *a, **kw):
        if self == src:
            raise OSError("simulated")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", fake_resolve):
        sources = discover_table_sources(tmp_path, tmp_path / "build")

    assert [s.name for s in sources] == ["t1.raw"]


# --- source_dirs / restoring a table deleted from disk ----------------------

def test_commit_records_relative_source_dir(tmp_path):
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    src = sensors / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)

    snap = history.commit("t", [src], [], "first")

    assert snap.source_dirs == {"t.raw": "sensors"}


def test_commit_records_empty_dir_for_root_level_source(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)

    snap = history.commit("t", [src], [], "first")

    assert snap.source_dirs == {"t.raw": ""}


def test_relative_dir_outside_project_root_falls_back_to_empty(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "t.raw"
    outside.write_text("x")
    history = HistoryStore(tmp_path)

    snap = history.commit("t", [outside], [], "first")

    assert snap.source_dirs == {"t.raw": ""}


def test_source_paths_for_snapshot_reconstructs_nested_path(tmp_path):
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    src = sensors / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "first")

    paths = history.source_paths_for_snapshot("t", 1)

    assert paths == [sensors / "t.raw"]


def test_source_paths_for_snapshot_legacy_snapshot_assumes_root(tmp_path):
    """A snapshot written before source_dirs existed doesn't have it —
    the fallback assumes the project root for every file instead of
    crashing."""
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "first")
    manifest_path = tmp_path / ".payload_history" / "tables" / "t.json"
    raw = json.loads(manifest_path.read_text())
    del raw[0]["source_dirs"]
    manifest_path.write_text(json.dumps(raw))

    paths = history.source_paths_for_snapshot("t", 1)

    assert paths == [tmp_path / "t.raw"]


def test_restore_recreates_missing_parent_directory(tmp_path):
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    src = sensors / "t.raw"
    src.write_text("original")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "first")

    src.unlink()
    sensors.rmdir()
    assert not sensors.exists()

    reconstructed_paths = history.source_paths_for_snapshot("t", 1)
    result = history.restore("t", 1, reconstructed_paths, tmp_path / "build")

    assert (sensors / "t.raw").read_text() == "original"
    assert (sensors / "t.raw") in result.written
