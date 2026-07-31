import re
from pathlib import Path

import pytest

from payload.core.errors import WriterEmitError
from payload.core.ir import TableIR
from payload.writers.header_writer import HeaderWriter

_VALID_C_IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def _ir(name="sensor_temp", data=b"\x01\x02\x03\x04", comments=None):
    return TableIR(
        name=name, data=data, source_path=Path("sensor_temp.raw"), source_format="fake",
        comments=comments or [],
    )


def test_array_contents_match_ir_data_exactly(tmp_path):
    ir = _ir(data=b"\x00\x01\xff\xab")
    out = HeaderWriter().emit(ir, tmp_path / "t.h", {})

    content = out.read_text()
    hex_values = re.findall(r"0x([0-9A-Fa-f]{2})", content)
    assert bytes(int(h, 16) for h in hex_values) == ir.data


def test_array_name_derived_from_table_name(tmp_path):
    ir = _ir(name="sensor_temp")
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()
    assert "static const uint8_t sensor_temp[4]" in content


def test_include_guard_derived_from_table_name(tmp_path):
    ir = _ir(name="sensor_temp")
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()
    assert "#ifndef SENSOR_TEMP_H" in content
    assert "#define SENSOR_TEMP_H" in content
    assert "#endif" in content
    assert "SENSOR_TEMP_H" in content.splitlines()[-1]


def test_array_name_override_via_plugin_config(tmp_path):
    ir = _ir()
    config = {"plugin": {"header": {"array_name": "custom_name"}}}
    content = HeaderWriter().emit(ir, tmp_path / "t.h", config).read_text()
    assert "custom_name[4]" in content


def test_include_guard_override_via_plugin_config(tmp_path):
    ir = _ir()
    config = {"plugin": {"header": {"include_guard": "MY_GUARD"}}}
    content = HeaderWriter().emit(ir, tmp_path / "t.h", config).read_text()
    assert "#ifndef MY_GUARD" in content


def test_cli_opts_override_wins_over_plugin_config(tmp_path):
    ir = _ir()
    config = {
        "plugin": {"header": {"array_name": "from_plugin"}},
        "cli_opts": {"array_name": "from_cli"},
    }
    content = HeaderWriter().emit(ir, tmp_path / "t.h", config).read_text()
    assert "from_cli[4]" in content
    assert "from_plugin" not in content


def test_comments_appear_as_trailing_line_comments(tmp_path):
    ir = _ir(data=b"\x01\x02\x03", comments=[(0, "first"), (2, "third")])
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()

    lines = [l for l in content.splitlines() if l.strip().startswith("0x")]
    assert lines[0].endswith("// first")
    assert not lines[1].rstrip().endswith(("first", "third"))
    assert lines[2].endswith("// third")


def test_table_name_starting_with_digit_is_prefixed(tmp_path):
    ir = _ir(name="3volt", data=b"\x01")
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()
    assert "table_3volt[1]" in content
    assert "#ifndef TABLE_3VOLT_H" in content


def test_table_name_with_only_invalid_chars_sanitized_safely(tmp_path):
    ir = _ir(name="???", data=b"\x01")
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()

    array_name = re.search(r"static const uint8_t (\w+)\[1\]", content).group(1)
    guard = re.search(r"#ifndef (\w+)", content).group(1)
    assert _VALID_C_IDENTIFIER.match(array_name)
    assert _VALID_C_IDENTIFIER.match(guard)


def test_empty_data_raises_writer_emit_error(tmp_path):
    ir = _ir(data=b"")
    with pytest.raises(WriterEmitError):
        HeaderWriter().emit(ir, tmp_path / "t.h", {})


def test_includes_stdint_header(tmp_path):
    ir = _ir()
    content = HeaderWriter().emit(ir, tmp_path / "t.h", {}).read_text()
    assert "#include <stdint.h>" in content
