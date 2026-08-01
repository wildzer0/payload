from pathlib import Path

import pytest

from payload.core.config import PayloadConfig, load_config
from payload.core.errors import InvalidConfigError


def test_default_config_has_sensible_values():
    c = PayloadConfig()
    assert c.defaults.writer is None  # nessuna preferenza: il reader può suggerirne uno
    assert c.toolchain.compiler == "gcc"


def test_model_dump_returns_plain_dict():
    c = PayloadConfig()
    d = c.model_dump()
    assert d == {
        "defaults": {
            "writer": None, "reader": None, "output_dir": "build",
            "cache_dir": ".payload_cache", "byte_order": "little",
        },
        "toolchain": {
            "compiler": "gcc", "compiler_flags": [], "objcopy": "objcopy",
            "objcopy_target": "", "objcopy_arch": "",
        },
        "plugin": {},
        "pipeline_stages": [],
        "batch_tables": [],
    }


def test_load_config_no_file_uses_defaults(tmp_path):
    c = load_config(tmp_path)
    assert c.defaults.writer is None


def test_load_config_reads_global_toml(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "hex"\n')
    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"


def test_sidecar_overrides_only_declared_keys(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "bin"\n[toolchain]\ncompiler = "gcc"\n'
    )
    src_dir = tmp_path / "sensors"
    src_dir.mkdir()
    src = src_dir / "temp.raw"
    src.write_text("x")
    (src_dir / "temp.config.toml").write_text('[defaults]\nwriter = "hex"\n')

    c = load_config(tmp_path, source_path=src)
    assert c.defaults.writer == "hex"       # override dal sidecar
    assert c.toolchain.compiler == "gcc"    # ereditato dal globale, non toccato dal sidecar


def test_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\ncampo_inventato = 1\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_wrong_type_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[toolchain]\ncompiler_flags = "non una lista"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_malformed_toml_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text("questo non e TOML [[[")
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_unknown_top_level_section_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[sezione_a_caso]\nx = 1\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_str_field_wrong_type_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text("[defaults]\nwriter = 123\n")
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_defaults_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('defaults = "non una tabella"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_toolchain_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('toolchain = "non una tabella"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_pipeline_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('pipeline = "non una tabella"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_pipeline_stages_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[pipeline]\nstages = "non una lista"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_invalid_byte_order_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nbyte_order = "middle"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


# --- [[batch_table]] -----------------------------------------------------


def test_batch_table_parsed_into_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )
    config = load_config(tmp_path)
    assert config.batch_tables == [{"name": "rows", "sources": ["ROW*.txt"]}]


def test_batch_table_with_overrides_and_stages(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\n'
        'name = "rows"\n'
        'sources = ["ROW1.txt", "ROW2.txt"]\n'
        'reader = "raw_text"\n'
        'writer = "bin"\n'
        'byte_order = "big"\n'
        'stages = [{ type = "reader", name = "raw_text" }, { type = "writer", name = "bin" }]\n'
    )
    config = load_config(tmp_path)
    assert config.batch_tables[0]["reader"] == "raw_text"
    assert config.batch_tables[0]["byte_order"] == "big"
    assert len(config.batch_tables[0]["stages"]) == 2


def test_batch_table_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('batch_table = "non una lista"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_missing_name_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[batch_table]]\nsources = ["ROW*.txt"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_missing_sources_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[batch_table]]\nname = "rows"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\nbogus = "x"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_stages_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\nstages = "non una lista"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_sources_not_a_list_of_strings_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = [1, 2]\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_no_batch_table_defaults_to_empty_list(tmp_path):
    config = load_config(tmp_path)
    assert config.batch_tables == []


def test_batch_table_entry_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('batch_table = ["non una tabella"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)
