from pathlib import Path

import pytest

from payload.core.batch_tables import BatchTable, resolve_batch_tables
from payload.core.cache import BuildCache
from payload.core.config import load_config
from payload.core.discovery import TableRef
from payload.core.errors import (
    BatchTableError,
    EmptySourceError,
    InvalidImportError,
    SourceNotFoundError,
    TableAlreadyExistsError,
)
from payload.core.table_admin import (
    delete_batch_member,
    delete_table,
    import_batch_member,
    import_many_single_tables,
    import_new_batch_table,
    import_single_table,
)


# --- import_single_table ---------------------------------------------------

def test_import_single_table_creates_new(tmp_path):
    r = import_single_table(tmp_path, b"hello", "t1.fake", [], [])
    assert r.created is True
    assert r.path == tmp_path / "t1.fake"
    assert r.path.read_bytes() == b"hello"


def test_import_single_table_accepts_extension_no_reader_handles_yet(tmp_path):
    """Import doesn't require a reader for the extension to already be
    installed — a project can accumulate tables before installing (or
    writing) the plugin that reads them (see the no-bundled-plugins
    refactor: a fresh project starts with zero readers). Whether
    anything can actually read it only matters at build time."""
    r = import_single_table(tmp_path, b"hello", "t1.unknown", [], [])
    assert r.path.read_bytes() == b"hello"


def test_import_single_table_name_collision_without_overwrite(tmp_path):
    existing = tmp_path / "t1.fake"
    existing.write_bytes(b"old")
    with pytest.raises(TableAlreadyExistsError):
        import_single_table(tmp_path, b"new", "t1.fake", [existing], [])
    assert existing.read_bytes() == b"old"


def test_import_single_table_overwrite_replaces_content(tmp_path):
    existing = tmp_path / "t1.fake"
    existing.write_bytes(b"old")
    r = import_single_table(tmp_path, b"new", "t1.fake", [existing], [], overwrite=True)
    assert r.created is False
    assert existing.read_bytes() == b"new"


def test_import_single_table_name_collision_with_batch_table(tmp_path):
    bt = BatchTable(name="t1", source_paths=[tmp_path / "a.fake"])
    with pytest.raises(TableAlreadyExistsError):
        import_single_table(tmp_path, b"new", "t1.fake", [], [bt])


@pytest.mark.parametrize("bad", ["", "../escape.fake", "a/b.fake", "a\\b.fake", ".", "..", ".hidden.fake"])
def test_import_single_table_rejects_unsafe_filename(tmp_path, bad):
    with pytest.raises(InvalidImportError):
        import_single_table(tmp_path, b"x", bad, [], [])


def test_import_single_table_rejects_empty_file(tmp_path):
    with pytest.raises(EmptySourceError):
        import_single_table(tmp_path, b"", "t1.fake", [], [])
    assert not (tmp_path / "t1.fake").exists()


# --- import_new_batch_table -------------------------------------------------

def test_import_new_batch_table_creates_files_and_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text("")
    bt = import_new_batch_table(
        tmp_path, {"ROW1.fake": b"a", "ROW2.fake": b"b"}, "rows", [], [],
    )
    assert bt.name == "rows"
    assert (tmp_path / "ROW1.fake").read_bytes() == b"a"
    assert (tmp_path / "ROW2.fake").read_bytes() == b"b"

    config = load_config(tmp_path)
    resolved = resolve_batch_tables(tmp_path, config)
    assert resolved[0].name == "rows"
    assert {p.name for p in resolved[0].source_paths} == {"ROW1.fake", "ROW2.fake"}


def test_import_new_batch_table_no_files_raises(tmp_path):
    with pytest.raises(BatchTableError):
        import_new_batch_table(tmp_path, {}, "rows", [], [])


def test_import_new_batch_table_name_collision(tmp_path):
    existing = tmp_path / "rows.fake"
    with pytest.raises(TableAlreadyExistsError):
        import_new_batch_table(tmp_path, {"a.fake": b"x"}, "rows", [existing], [])


def test_import_new_batch_table_accepts_extension_no_reader_handles_yet(tmp_path):
    (tmp_path / "table-tool.toml").write_text("")
    bt = import_new_batch_table(tmp_path, {"a.unknown": b"x"}, "rows", [], [])
    assert (tmp_path / "a.unknown").read_bytes() == b"x"
    assert bt.name == "rows"


def test_import_new_batch_table_rejects_empty_file(tmp_path):
    with pytest.raises(EmptySourceError):
        import_new_batch_table(tmp_path, {"ROW1.fake": b"a", "ROW2.fake": b""}, "rows", [], [])
    assert not (tmp_path / "ROW1.fake").exists()
    assert not (tmp_path / "ROW2.fake").exists()


def test_import_new_batch_table_member_filename_collision_preserves_existing_file(tmp_path):
    """A member filename that collides with an already-tracked table's
    source must NOT be silently overwritten and folded into the new
    batch — regression test for a data-loss bug where dragging in two
    files, one sharing its name with an existing table, erased that
    table with no confirmation."""
    existing = tmp_path / "sensor.fake"
    existing.write_bytes(b"old")
    with pytest.raises(TableAlreadyExistsError):
        import_new_batch_table(
            tmp_path, {"sensor.fake": b"new", "other.fake": b"y"}, "combo", [existing], [],
        )
    assert existing.read_bytes() == b"old"
    assert not (tmp_path / "other.fake").exists()


# --- import_many_single_tables ----------------------------------------------

def test_import_many_single_tables_all_succeed(tmp_path):
    result = import_many_single_tables(
        tmp_path, {"a.fake": b"1", "b.fake": b"2", "c.fake": b"3"}, [], [],
    )
    assert {r.path.name for r in result.imported} == {"a.fake", "b.fake", "c.fake"}
    assert all(r.created for r in result.imported)
    assert result.skipped == []
    assert (tmp_path / "a.fake").read_bytes() == b"1"


def test_import_many_single_tables_accepts_extension_no_reader_handles_yet(tmp_path):
    result = import_many_single_tables(tmp_path, {"a.unknown": b"1"}, [], [])
    assert [r.path.name for r in result.imported] == ["a.unknown"]
    assert result.skipped == []


def test_import_many_single_tables_skips_existing_name_without_blocking_others(tmp_path):
    existing = tmp_path / "a.fake"
    existing.write_bytes(b"old")

    result = import_many_single_tables(
        tmp_path, {"a.fake": b"new", "b.fake": b"2"}, [existing], [],
    )

    assert [r.path.name for r in result.imported] == ["b.fake"]
    assert len(result.skipped) == 1
    assert result.skipped[0].filename == "a.fake"
    assert "already exists" in result.skipped[0].reason
    assert existing.read_bytes() == b"old"
    assert (tmp_path / "b.fake").read_bytes() == b"2"


def test_import_many_single_tables_overwrite_replaces_existing(tmp_path):
    existing = tmp_path / "a.fake"
    existing.write_bytes(b"old")

    result = import_many_single_tables(
        tmp_path, {"a.fake": b"new"}, [existing], [], overwrite=True,
    )

    assert len(result.imported) == 1
    assert result.imported[0].created is False
    assert result.skipped == []
    assert existing.read_bytes() == b"new"


def test_import_many_single_tables_skips_empty_without_blocking_others(tmp_path):
    result = import_many_single_tables(
        tmp_path, {"a.fake": b"1", "c.fake": b""}, [], [],
    )

    assert [r.path.name for r in result.imported] == ["a.fake"]
    assert [s.filename for s in result.skipped] == ["c.fake"]
    assert not (tmp_path / "c.fake").exists()


def test_import_many_single_tables_skips_unsafe_filename(tmp_path):
    result = import_many_single_tables(
        tmp_path, {"../escape.fake": b"x", "a.fake": b"1"}, [], [],
    )
    assert [r.path.name for r in result.imported] == ["a.fake"]
    assert [s.filename for s in result.skipped] == ["../escape.fake"]


def test_import_many_single_tables_duplicate_stem_within_upload_second_skipped(tmp_path):
    """Two uploaded files that resolve to the same table name (same
    stem, different extension): the first one imported claims the
    name, the second is reported as a collision rather than silently
    overwriting or being written under a mangled name."""
    result = import_many_single_tables(
        tmp_path, {"a.fake": b"1", "a.fake2": b"2"}, [], [],
    )
    assert len(result.imported) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].filename == "a.fake2"


def test_import_many_single_tables_empty_input_returns_empty_result(tmp_path):
    result = import_many_single_tables(tmp_path, {}, [], [])
    assert result.imported == []
    assert result.skipped == []


# --- import_batch_member ----------------------------------------------------

def test_import_batch_member_adds_file_and_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]

    target = import_batch_member(tmp_path, b"b", "ROW2.fake", bt)

    assert target == tmp_path / "ROW2.fake"
    assert target.read_bytes() == b"b"
    config2 = load_config(tmp_path)
    resolved = resolve_batch_tables(tmp_path, config2)[0]
    assert {p.name for p in resolved.source_paths} == {"ROW1.fake", "ROW2.fake"}


def test_import_batch_member_duplicate_filename_raises(tmp_path):
    bt = BatchTable(name="rows", source_paths=[tmp_path / "ROW1.fake"])
    with pytest.raises(TableAlreadyExistsError):
        import_batch_member(tmp_path, b"x", "ROW1.fake", bt)


def test_import_batch_member_rejects_unsafe_filename(tmp_path):
    bt = BatchTable(name="rows", source_paths=[])
    with pytest.raises(InvalidImportError):
        import_batch_member(tmp_path, b"x", "../escape.fake", bt)


def test_import_batch_member_rejects_empty_file(tmp_path):
    bt = BatchTable(name="rows", source_paths=[tmp_path / "ROW1.fake"])
    with pytest.raises(EmptySourceError):
        import_batch_member(tmp_path, b"", "ROW2.fake", bt)
    assert not (tmp_path / "ROW2.fake").exists()


# --- delete_table ------------------------------------------------------------

def _ref(name, paths, is_batch=False, batch=None):
    return TableRef(name=name, source_paths=paths, is_batch=is_batch, batch=batch)


def test_delete_table_removes_source_output_and_cache(tmp_path):
    src = tmp_path / "t1.fake"
    src.write_bytes(b"x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t1.fakeout"
    out.write_bytes(b"y")
    cache = BuildCache(tmp_path / ".cache")
    cache.update("t1", "somehash", out)

    result = delete_table(tmp_path, _ref("t1", [src]), out_dir, cache)

    assert result.removed_sources == [src]
    assert result.removed_outputs == [out]
    assert result.batch_entry_removed is False
    assert not src.exists()
    assert not out.exists()
    assert cache.is_fresh("t1", "somehash") is False


def test_delete_table_batch_removes_config_entry(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake", "ROW2.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    (tmp_path / "ROW2.fake").write_bytes(b"b")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]
    out_dir = tmp_path / "build"
    cache = BuildCache(tmp_path / ".cache")

    result = delete_table(tmp_path, _ref("rows", bt.source_paths, is_batch=True, batch=bt), out_dir, cache)

    assert result.batch_entry_removed is True
    assert not (tmp_path / "ROW1.fake").exists()
    assert not (tmp_path / "ROW2.fake").exists()
    config2 = load_config(tmp_path)
    assert resolve_batch_tables(tmp_path, config2) == []


# --- delete_batch_member -----------------------------------------------------

def test_delete_batch_member_keeps_entry_when_others_remain(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake", "ROW2.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    (tmp_path / "ROW2.fake").write_bytes(b"b")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]
    cache = BuildCache(tmp_path / ".cache")

    result = delete_batch_member(tmp_path, bt, "ROW1.fake", tmp_path / "build", cache)

    assert result.batch_entry_removed is False
    assert not (tmp_path / "ROW1.fake").exists()
    assert (tmp_path / "ROW2.fake").exists()
    config2 = load_config(tmp_path)
    resolved = resolve_batch_tables(tmp_path, config2)[0]
    assert [p.name for p in resolved.source_paths] == ["ROW2.fake"]


def test_delete_batch_member_last_one_removes_whole_entry(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "rows.fakeout").write_bytes(b"out")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]
    cache = BuildCache(tmp_path / ".cache")

    result = delete_batch_member(tmp_path, bt, "ROW1.fake", out_dir, cache)

    assert result.batch_entry_removed is True
    assert result.removed_outputs == [out_dir / "rows.fakeout"]
    assert not (out_dir / "rows.fakeout").exists()
    config2 = load_config(tmp_path)
    assert resolve_batch_tables(tmp_path, config2) == []


def test_delete_batch_member_glob_source_needs_no_config_edit(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    (tmp_path / "ROW2.fake").write_bytes(b"b")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]
    cache = BuildCache(tmp_path / ".cache")

    result = delete_batch_member(tmp_path, bt, "ROW1.fake", tmp_path / "build", cache)

    assert result.batch_entry_removed is False
    config2 = load_config(tmp_path)
    resolved = resolve_batch_tables(tmp_path, config2)[0]
    assert [p.name for p in resolved.source_paths] == ["ROW2.fake"]


def test_delete_batch_member_unknown_filename_raises(tmp_path):
    bt = BatchTable(name="rows", source_paths=[tmp_path / "ROW1.fake"])
    cache = BuildCache(tmp_path / ".cache")
    with pytest.raises(SourceNotFoundError):
        delete_batch_member(tmp_path, bt, "does_not_exist.fake", tmp_path / "build", cache)


def test_delete_batch_member_outside_project_root_falls_back_to_name(tmp_path, tmp_path_factory):
    """Purely defensive case: source_paths should always live under
    project_root (it always comes from discovery), but if for some
    reason it didn't, attempting to compute a relative path must not
    blow up — it degrades to just the filename."""
    outside_dir = tmp_path_factory.mktemp("outside")
    outside_file = outside_dir / "ROW1.fake"
    outside_file.write_bytes(b"a")
    (tmp_path / "ROW2.fake").write_bytes(b"b")
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake", "ROW2.fake"]\n'
    )
    bt = BatchTable(name="rows", source_paths=[outside_file, tmp_path / "ROW2.fake"])
    cache = BuildCache(tmp_path / ".cache")

    result = delete_batch_member(tmp_path, bt, "ROW1.fake", tmp_path / "build", cache)

    assert result.batch_entry_removed is False
    assert not outside_file.exists()
