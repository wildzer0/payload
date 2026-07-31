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
            "writer": None, "output_dir": "build", "golden_dir": "golden",
            "cache_dir": ".payload_cache", "byte_order": "little",
        },
        "toolchain": {
            "compiler": "gcc", "compiler_flags": [], "objcopy": "objcopy",
            "objcopy_target": "", "objcopy_arch": "",
        },
        "plugin": {},
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
