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
    NoReaderFoundError,
    SourceNotFoundError,
    TableAlreadyExistsError,
)
from payload.core.registry import PluginRegistry
from payload.core.table_admin import (
    delete_batch_member,
    delete_table,
    import_batch_member,
    import_new_batch_table,
    import_single_table,
)
from tests.fakes import FakeReader


def _registry():
    r = PluginRegistry()
    r.register_reader(FakeReader())
    return r


# --- import_single_table ---------------------------------------------------

def test_import_single_table_creates_new(tmp_path):
    r = import_single_table(tmp_path, _registry(), b"hello", "t1.fake", [], [])
    assert r.created is True
    assert r.path == tmp_path / "t1.fake"
    assert r.path.read_bytes() == b"hello"


def test_import_single_table_rejects_unknown_extension(tmp_path):
    with pytest.raises(NoReaderFoundError):
        import_single_table(tmp_path, _registry(), b"hello", "t1.unknown", [], [])
    assert not (tmp_path / "t1.unknown").exists()


def test_import_single_table_name_collision_without_overwrite(tmp_path):
    existing = tmp_path / "t1.fake"
    existing.write_bytes(b"old")
    with pytest.raises(TableAlreadyExistsError):
        import_single_table(tmp_path, _registry(), b"new", "t1.fake", [existing], [])
    assert existing.read_bytes() == b"old"


def test_import_single_table_overwrite_replaces_content(tmp_path):
    existing = tmp_path / "t1.fake"
    existing.write_bytes(b"old")
    r = import_single_table(tmp_path, _registry(), b"new", "t1.fake", [existing], [], overwrite=True)
    assert r.created is False
    assert existing.read_bytes() == b"new"


def test_import_single_table_name_collision_with_batch_table(tmp_path):
    bt = BatchTable(name="t1", source_paths=[tmp_path / "a.fake"])
    with pytest.raises(TableAlreadyExistsError):
        import_single_table(tmp_path, _registry(), b"new", "t1.fake", [], [bt])


@pytest.mark.parametrize("bad", ["", "../escape.fake", "a/b.fake", "a\\b.fake", ".", "..", ".hidden.fake"])
def test_import_single_table_rejects_unsafe_filename(tmp_path, bad):
    with pytest.raises(InvalidImportError):
        import_single_table(tmp_path, _registry(), b"x", bad, [], [])


def test_import_single_table_rejects_empty_file(tmp_path):
    with pytest.raises(EmptySourceError):
        import_single_table(tmp_path, _registry(), b"", "t1.fake", [], [])
    assert not (tmp_path / "t1.fake").exists()


# --- import_new_batch_table -------------------------------------------------

def test_import_new_batch_table_creates_files_and_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text("")
    bt = import_new_batch_table(
        tmp_path, _registry(), {"ROW1.fake": b"a", "ROW2.fake": b"b"}, "rows", [], [],
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
        import_new_batch_table(tmp_path, _registry(), {}, "rows", [], [])


def test_import_new_batch_table_name_collision(tmp_path):
    existing = tmp_path / "rows.fake"
    with pytest.raises(TableAlreadyExistsError):
        import_new_batch_table(tmp_path, _registry(), {"a.fake": b"x"}, "rows", [existing], [])


def test_import_new_batch_table_rejects_unreadable_extension(tmp_path):
    with pytest.raises(NoReaderFoundError):
        import_new_batch_table(tmp_path, _registry(), {"a.unknown": b"x"}, "rows", [], [])
    assert not (tmp_path / "a.unknown").exists()


def test_import_new_batch_table_rejects_empty_file(tmp_path):
    with pytest.raises(EmptySourceError):
        import_new_batch_table(tmp_path, _registry(), {"ROW1.fake": b"a", "ROW2.fake": b""}, "rows", [], [])
    assert not (tmp_path / "ROW1.fake").exists()
    assert not (tmp_path / "ROW2.fake").exists()


# --- import_batch_member ----------------------------------------------------

def test_import_batch_member_adds_file_and_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.fake"]\n'
    )
    (tmp_path / "ROW1.fake").write_bytes(b"a")
    config = load_config(tmp_path)
    bt = resolve_batch_tables(tmp_path, config)[0]

    target = import_batch_member(tmp_path, _registry(), b"b", "ROW2.fake", bt)

    assert target == tmp_path / "ROW2.fake"
    assert target.read_bytes() == b"b"
    config2 = load_config(tmp_path)
    resolved = resolve_batch_tables(tmp_path, config2)[0]
    assert {p.name for p in resolved.source_paths} == {"ROW1.fake", "ROW2.fake"}


def test_import_batch_member_duplicate_filename_raises(tmp_path):
    bt = BatchTable(name="rows", source_paths=[tmp_path / "ROW1.fake"])
    with pytest.raises(TableAlreadyExistsError):
        import_batch_member(tmp_path, _registry(), b"x", "ROW1.fake", bt)


def test_import_batch_member_rejects_unsafe_filename(tmp_path):
    bt = BatchTable(name="rows", source_paths=[])
    with pytest.raises(InvalidImportError):
        import_batch_member(tmp_path, _registry(), b"x", "../escape.fake", bt)


def test_import_batch_member_rejects_empty_file(tmp_path):
    bt = BatchTable(name="rows", source_paths=[tmp_path / "ROW1.fake"])
    with pytest.raises(EmptySourceError):
        import_batch_member(tmp_path, _registry(), b"", "ROW2.fake", bt)
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
