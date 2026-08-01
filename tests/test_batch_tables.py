from pathlib import Path

import pytest

from payload.core.batch_tables import BatchTable, _natural_sort_key, effective_config, resolve_batch_tables
from payload.core.config import PayloadConfig, load_config
from payload.core.errors import BatchTableError


def _config_with_batch(tmp_path: Path, toml_text: str):
    (tmp_path / "table-tool.toml").write_text(toml_text)
    return load_config(tmp_path)


def test_resolve_batch_table_literal_sources_preserve_order(tmp_path):
    (tmp_path / "ROW2.txt").write_text("b")
    (tmp_path / "ROW1.txt").write_text("a")
    config = _config_with_batch(
        tmp_path,
        '[[batch_table]]\nname = "rows"\nsources = ["ROW2.txt", "ROW1.txt"]\n',
    )

    tables = resolve_batch_tables(tmp_path, config)

    assert len(tables) == 1
    assert [p.name for p in tables[0].source_paths] == ["ROW2.txt", "ROW1.txt"]


def test_resolve_batch_table_glob_sources_natural_sorted(tmp_path):
    for n in (1, 2, 10):
        (tmp_path / f"ROW{n}.txt").write_text(str(n))
    config = _config_with_batch(
        tmp_path, '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )

    tables = resolve_batch_tables(tmp_path, config)

    assert [p.name for p in tables[0].source_paths] == ["ROW1.txt", "ROW2.txt", "ROW10.txt"]


def test_resolve_batch_table_mixes_literal_and_glob(tmp_path):
    (tmp_path / "extra.txt").write_text("x")
    (tmp_path / "ROW1.txt").write_text("1")
    (tmp_path / "ROW2.txt").write_text("2")
    config = _config_with_batch(
        tmp_path, '[[batch_table]]\nname = "rows"\nsources = ["extra.txt", "ROW*.txt"]\n'
    )

    tables = resolve_batch_tables(tmp_path, config)

    assert [p.name for p in tables[0].source_paths] == ["extra.txt", "ROW1.txt", "ROW2.txt"]


def test_resolve_batch_table_carries_overrides_and_stages(tmp_path):
    (tmp_path / "ROW1.txt").write_text("1")
    config = _config_with_batch(
        tmp_path,
        '[[batch_table]]\n'
        'name = "rows"\n'
        'sources = ["ROW1.txt"]\n'
        'reader = "raw_text"\n'
        'writer = "bin"\n'
        'byte_order = "big"\n'
        'stages = [{ type = "reader", name = "raw_text" }]\n',
    )

    tables = resolve_batch_tables(tmp_path, config)

    t = tables[0]
    assert t.reader == "raw_text"
    assert t.writer == "bin"
    assert t.byte_order == "big"
    assert t.stages == [{"type": "reader", "name": "raw_text"}]


def test_resolve_batch_table_no_stages_is_none(tmp_path):
    (tmp_path / "ROW1.txt").write_text("1")
    config = _config_with_batch(
        tmp_path, '[[batch_table]]\nname = "rows"\nsources = ["ROW1.txt"]\n'
    )
    assert resolve_batch_tables(tmp_path, config)[0].stages is None


def test_no_batch_tables_returns_empty_list(tmp_path):
    config = load_config(tmp_path)
    assert resolve_batch_tables(tmp_path, config) == []


def test_literal_source_missing_raises(tmp_path):
    config = _config_with_batch(
        tmp_path, '[[batch_table]]\nname = "rows"\nsources = ["nope.txt"]\n'
    )
    with pytest.raises(BatchTableError, match="nope.txt"):
        resolve_batch_tables(tmp_path, config)


def test_glob_resolving_to_nothing_raises(tmp_path):
    config = _config_with_batch(
        tmp_path, '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )
    with pytest.raises(BatchTableError, match="rows"):
        resolve_batch_tables(tmp_path, config)


def test_duplicate_filename_within_batch_raises(tmp_path):
    (tmp_path / "sub1").mkdir()
    (tmp_path / "sub2").mkdir()
    (tmp_path / "sub1" / "ROW1.txt").write_text("a")
    (tmp_path / "sub2" / "ROW1.txt").write_text("b")
    config = _config_with_batch(
        tmp_path,
        '[[batch_table]]\nname = "rows"\nsources = ["sub1/ROW1.txt", "sub2/ROW1.txt"]\n',
    )
    with pytest.raises(BatchTableError, match="ROW1.txt"):
        resolve_batch_tables(tmp_path, config)


def test_duplicate_batch_table_name_raises(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    config = _config_with_batch(
        tmp_path,
        '[[batch_table]]\nname = "rows"\nsources = ["a.txt"]\n'
        '[[batch_table]]\nname = "rows"\nsources = ["b.txt"]\n',
    )
    with pytest.raises(BatchTableError, match="duplicate"):
        resolve_batch_tables(tmp_path, config)


def test_natural_sort_key_orders_numeric_suffixes():
    paths = [Path("ROW10.txt"), Path("ROW2.txt"), Path("ROW1.txt")]
    assert sorted(paths, key=_natural_sort_key) == [Path("ROW1.txt"), Path("ROW2.txt"), Path("ROW10.txt")]


def test_batch_table_dataclass_defaults():
    bt = BatchTable(name="rows", source_paths=[Path("a.txt")])
    assert bt.reader is None
    assert bt.writer is None
    assert bt.byte_order is None
    assert bt.stages is None


# --- effective_config --------------------------------------------------------


def test_effective_config_overlays_batch_overrides_on_defaults():
    base = PayloadConfig()
    bt = BatchTable(name="rows", source_paths=[Path("a.txt")], reader="raw_text", writer="bin", byte_order="big")

    cfg = effective_config(base, bt)

    assert cfg.defaults.reader == "raw_text"
    assert cfg.defaults.writer == "bin"
    assert cfg.defaults.byte_order == "big"


def test_effective_config_falls_back_to_base_when_batch_has_no_override():
    from dataclasses import replace as dc_replace

    base = dc_replace(PayloadConfig(), defaults=dc_replace(PayloadConfig().defaults, writer="hex"))
    bt = BatchTable(name="rows", source_paths=[Path("a.txt")])

    cfg = effective_config(base, bt)

    assert cfg.defaults.writer == "hex"


def test_effective_config_uses_batch_stages_when_present():
    base = PayloadConfig()
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "bin"}]
    bt = BatchTable(name="rows", source_paths=[Path("a.txt")], stages=stages)

    cfg = effective_config(base, bt)

    assert cfg.pipeline_stages == stages


def test_effective_config_falls_back_to_base_pipeline_stages_when_batch_has_none():
    from dataclasses import replace as dc_replace

    base_stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "bin"}]
    base = dc_replace(PayloadConfig(), pipeline_stages=base_stages)
    bt = BatchTable(name="rows", source_paths=[Path("a.txt")])

    cfg = effective_config(base, bt)

    assert cfg.pipeline_stages == base_stages
