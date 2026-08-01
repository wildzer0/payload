from pathlib import Path

import pytest

from payload.core.config import (
    DefaultsConfig,
    ToolchainConfig,
    config_schema,
    delete_sidecar_config,
    load_config,
    read_raw_sidecar,
    write_global_config,
    write_sidecar_config,
)
from payload.core.errors import InvalidConfigError


def _defaults(**overrides) -> dict:
    return {**DefaultsConfig().__dict__, **overrides}


def _toolchain(**overrides) -> dict:
    return {**ToolchainConfig().__dict__, **overrides}


# --- write_global_config ---------------------------------------------------


def test_write_global_config_round_trips(tmp_path):
    write_global_config(tmp_path, _defaults(writer="hex"), _toolchain(compiler="clang"))

    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"
    assert c.toolchain.compiler == "clang"


def test_write_global_config_preserves_plugin_and_pipeline_sections(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "bin"\n'
        '[plugin.custom]\nkey = "value"\n'
        '[pipeline]\nstages = [{ type = "reader", name = "raw_text" }, { type = "writer", name = "bin" }]\n'
    )

    write_global_config(tmp_path, _defaults(writer="hex"), _toolchain())

    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"
    assert c.plugin == {"custom": {"key": "value"}}
    assert len(c.pipeline_stages) == 2


def test_write_global_config_rejects_unknown_field_without_writing(tmp_path):
    path = tmp_path / "table-tool.toml"
    with pytest.raises(InvalidConfigError):
        write_global_config(tmp_path, {**_defaults(), "campo_inventato": 1}, _toolchain())
    assert not path.exists()


def test_write_global_config_rejects_invalid_byte_order(tmp_path):
    with pytest.raises(InvalidConfigError):
        write_global_config(tmp_path, _defaults(byte_order="middle"), _toolchain())


def test_write_global_config_overwrites_previous_values(tmp_path):
    write_global_config(tmp_path, _defaults(writer="hex"), _toolchain())
    write_global_config(tmp_path, _defaults(writer="bin"), _toolchain())

    assert load_config(tmp_path).defaults.writer == "bin"


# --- sidecar -----------------------------------------------------------


def _source(tmp_path: Path) -> Path:
    src_dir = tmp_path / "sensors"
    src_dir.mkdir()
    src = src_dir / "temp.raw"
    src.write_text("x")
    return src


def test_read_raw_sidecar_missing_returns_empty_dict(tmp_path):
    src = _source(tmp_path)
    assert read_raw_sidecar(src) == {}


def test_write_sidecar_config_round_trips_defaults(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"})

    assert read_raw_sidecar(src) == {"defaults": {"writer": "hex"}}
    c = load_config(tmp_path, source_path=src)
    assert c.defaults.writer == "hex"


def test_write_sidecar_config_with_pipeline_stages(tmp_path):
    src = _source(tmp_path)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    write_sidecar_config(src, pipeline_stages=stages)

    c = load_config(tmp_path, source_path=src)
    assert c.pipeline_stages == stages


def test_write_sidecar_config_none_leaves_other_sections_untouched(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"})
    write_sidecar_config(src, toolchain={"compiler": "clang"})

    raw = read_raw_sidecar(src)
    assert raw["defaults"] == {"writer": "hex"}
    assert raw["toolchain"] == {"compiler": "clang"}


def test_write_sidecar_config_empty_dict_removes_only_that_section(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"}, toolchain={"compiler": "clang"})
    write_sidecar_config(src, defaults={})

    raw = read_raw_sidecar(src)
    assert "defaults" not in raw
    assert raw["toolchain"] == {"compiler": "clang"}


def test_write_sidecar_config_empty_toolchain_removes_only_that_section(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"}, toolchain={"compiler": "clang"})
    write_sidecar_config(src, toolchain={})

    raw = read_raw_sidecar(src)
    assert raw["defaults"] == {"writer": "hex"}
    assert "toolchain" not in raw


def test_write_sidecar_config_fully_empty_deletes_file(tmp_path):
    src = _source(tmp_path)
    sidecar_path = write_sidecar_config(src, defaults={"writer": "hex"})
    assert sidecar_path.exists()

    write_sidecar_config(src, defaults={})

    assert not sidecar_path.exists()


def test_write_sidecar_config_drops_none_toolchain_values(tmp_path):
    src = _source(tmp_path)
    # riflette il form web: un campo con lo switch "sovrascrivi" attivo
    # ma lasciato vuoto arriva come None, non deve rompere la
    # validazione (deve essere trattato come "non impostato").
    write_sidecar_config(src, toolchain={"compiler": "clang", "objcopy": None})

    raw = read_raw_sidecar(src)
    assert raw["toolchain"] == {"compiler": "clang"}


def test_write_sidecar_config_preserves_plugin_section(tmp_path):
    src = _source(tmp_path)
    (src.parent / "temp.config.toml").write_text('[plugin.custom]\nkey = "value"\n')

    write_sidecar_config(src, defaults={"writer": "hex"})

    raw = read_raw_sidecar(src)
    assert raw["plugin"] == {"custom": {"key": "value"}}
    assert raw["defaults"] == {"writer": "hex"}


def test_write_sidecar_config_rejects_unknown_field(tmp_path):
    src = _source(tmp_path)
    with pytest.raises(InvalidConfigError):
        write_sidecar_config(src, defaults={"campo_inventato": 1})


def test_write_sidecar_config_rejects_invalid_byte_order(tmp_path):
    src = _source(tmp_path)
    with pytest.raises(InvalidConfigError):
        write_sidecar_config(src, defaults={"byte_order": "middle"})


def test_delete_sidecar_config(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"})

    assert delete_sidecar_config(src) is True
    assert delete_sidecar_config(src) is False  # idempotente


# --- config_schema -----------------------------------------------------


def test_config_schema_lists_known_fields_with_types():
    schema = config_schema()

    defaults_keys = {f["key"] for f in schema["defaults"]}
    assert defaults_keys == {"writer", "reader", "output_dir", "cache_dir", "byte_order"}
    assert all(f["type"] == "string" for f in schema["defaults"])

    toolchain_by_key = {f["key"]: f["type"] for f in schema["toolchain"]}
    assert toolchain_by_key["compiler_flags"] == "list"
    assert toolchain_by_key["compiler"] == "string"
