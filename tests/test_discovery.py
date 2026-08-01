from pathlib import Path

import pytest

from payload.core.batch_tables import BatchTable
from payload.core.discovery import (
    TableRef,
    all_table_refs,
    discover_for_history,
    exclude_batch_members,
    resolve_table_ref,
)
from payload.core.errors import DuplicateTableNameError

RAW_READER_CONTENT = "0x01, 0x02\n"


def _init_project(tmp_path: Path) -> Path:
    (tmp_path / "table-tool.toml").touch()
    return tmp_path


def test_discover_for_history_returns_empty_batch_tables_when_none_declared(tmp_path):
    _init_project(tmp_path)
    (tmp_path / "a.raw").write_text(RAW_READER_CONTENT)

    sources, batch_tables, config = discover_for_history(tmp_path)

    assert [p.name for p in sources] == ["a.raw"]
    assert batch_tables == []


def test_discover_for_history_includes_resolved_batch_tables(tmp_path):
    (tmp_path / "ROW1.txt").write_text(RAW_READER_CONTENT)
    (tmp_path / "ROW2.txt").write_text(RAW_READER_CONTENT)
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )

    sources, batch_tables, config = discover_for_history(tmp_path)

    assert len(batch_tables) == 1
    assert batch_tables[0].name == "rows"
    assert [p.name for p in batch_tables[0].source_paths] == ["ROW1.txt", "ROW2.txt"]


def test_discover_for_history_excludes_batch_member_files_from_standalone_discovery(tmp_path):
    """Regression: ROW1.txt/ROW2.txt have an extension (.txt) recognized
    by a real reader (raw_text) — without the exclusion, they'd be
    discovered TWICE: as part of the 'rows' batch and as standalone
    tables 'ROW1'/'ROW2', with duplicated build/output."""
    (tmp_path / "ROW1.txt").write_text(RAW_READER_CONTENT)
    (tmp_path / "ROW2.txt").write_text(RAW_READER_CONTENT)
    (tmp_path / "other.raw").write_text(RAW_READER_CONTENT)
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )

    sources, batch_tables, config = discover_for_history(tmp_path)

    assert [p.name for p in sources] == ["other.raw"]
    assert len(batch_tables) == 1


def test_exclude_batch_members_removes_member_paths():
    bt = BatchTable(name="rows", source_paths=[Path("ROW1.txt"), Path("ROW2.txt")])
    sources = [Path("ROW1.txt"), Path("ROW2.txt"), Path("other.raw")]

    result = exclude_batch_members(sources, [bt])

    assert [p.name for p in result] == ["other.raw"]


def test_exclude_batch_members_noop_without_batch_tables():
    sources = [Path("a.raw"), Path("b.raw")]
    assert exclude_batch_members(sources, []) == sources


def test_discover_for_history_rejects_batch_name_colliding_with_real_file(tmp_path):
    (tmp_path / "rows.raw").write_text(RAW_READER_CONTENT)
    (tmp_path / "ROW1.txt").write_text(RAW_READER_CONTENT)
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW1.txt"]\n'
    )

    with pytest.raises(DuplicateTableNameError):
        discover_for_history(tmp_path)


def test_discover_for_history_still_rejects_plain_file_collisions(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "dup.raw").write_text(RAW_READER_CONTENT)
    (tmp_path / "b" / "dup.raw").write_text(RAW_READER_CONTENT)
    _init_project(tmp_path)

    with pytest.raises(DuplicateTableNameError):
        discover_for_history(tmp_path)


# --- resolve_table_ref -----------------------------------------------------


def test_resolve_table_ref_finds_regular_file():
    sources = [Path("a.raw"), Path("b.raw")]
    ref = resolve_table_ref(sources, [], "b")
    assert ref == TableRef(name="b", source_paths=[Path("b.raw")], is_batch=False)


def test_resolve_table_ref_finds_batch_table():
    bt = BatchTable(name="rows", source_paths=[Path("ROW1.txt"), Path("ROW2.txt")])
    ref = resolve_table_ref([], [bt], "rows")
    assert ref == TableRef(name="rows", source_paths=bt.source_paths, is_batch=True, batch=bt)
    assert ref.batch is bt


def test_resolve_table_ref_batch_takes_priority_when_names_would_collide():
    """In practice check_no_batch_name_collisions prevents this from
    really happening, but resolve_table_ref on its own is deliberately
    ordered batch-first: a batch table's name is declared explicitly,
    not derived from a filename."""
    bt = BatchTable(name="rows", source_paths=[Path("ROW1.txt")])
    ref = resolve_table_ref([Path("rows.raw")], [bt], "rows")
    assert ref.is_batch is True


def test_resolve_table_ref_returns_none_when_not_found():
    assert resolve_table_ref([Path("a.raw")], [], "missing") is None


# --- all_table_refs ---------------------------------------------------------


def test_all_table_refs_combines_files_and_batch_tables():
    bt = BatchTable(name="rows", source_paths=[Path("ROW1.txt")])
    refs = all_table_refs([Path("a.raw"), Path("b.raw")], [bt])

    assert [r.name for r in refs] == ["a", "b", "rows"]
    assert [r.is_batch for r in refs] == [False, False, True]


def test_all_table_refs_empty_when_nothing_discovered():
    assert all_table_refs([], []) == []
