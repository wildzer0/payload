"""
obj_writer tests with subprocess MOCKED — see
test_c_source_reader_mocked.py for the same reasoning: cover every
branch without depending on a real objcopy installed on the machine
running the tests (test_c_source_and_obj.py remains the integration
test with a real toolchain, skipped if unavailable)."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.errors import ToolchainExecutionError, WriterEmitError
from payload.core.ir import TableIR
from tests.example_plugins_helper import load_example_plugin

_obj_writer_module = load_example_plugin("obj_writer.py")
ObjWriter = _obj_writer_module.ObjWriter
section_name_for = _obj_writer_module.section_name_for


def _ir(name="sensor", data=b"\x0a\x1b\x2c\x3d") -> TableIR:
    return TableIR(name=name, data=data, source_path=Path("sensor.c"), source_format="c_source")


def test_missing_objcopy_target_raises(tmp_path):
    with pytest.raises(WriterEmitError):
        ObjWriter().emit(_ir(), tmp_path / "out.o", {"plugin": {"obj": {}}})


def test_missing_objcopy_arch_raises(tmp_path):
    with pytest.raises(WriterEmitError):
        ObjWriter().emit(_ir(), tmp_path / "out.o", {"plugin": {"obj": {"objcopy_target": "elf32-littlearm"}}})


def test_objcopy_failure_raises_toolchain_error(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrelevant")
    ir = TableIR(name="sensor", data=b"\x0a", source_path=source, source_format="c_source")
    config = {"plugin": {"obj": {"objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}}

    def fake_run(cmd, capture_output=True, text=True):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="objcopy failed")

    with patch.object(_obj_writer_module.subprocess, "run", fake_run):
        with pytest.raises(ToolchainExecutionError):
            ObjWriter().emit(ir, tmp_path / "out.o", config)


def test_objcopy_not_found_raises_toolchain_error_not_bare_exception(tmp_path):
    """subprocess.run() raises FileNotFoundError if the executable
    genuinely doesn't exist (not a nonzero returncode) — this wasn't
    handled before, it reached the user as a raw Python traceback."""
    source = tmp_path / "sensor.c"
    source.write_text("irrelevant")
    ir = TableIR(name="sensor", data=b"\x0a", source_path=source, source_format="c_source")
    config = {"plugin": {"obj": {"objcopy": "objcopy-that-does-not-exist", "objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}}

    def fake_run(cmd, capture_output=True, text=True):
        raise FileNotFoundError(2, "No such file or directory", "objcopy-that-does-not-exist")

    with patch.object(_obj_writer_module.subprocess, "run", fake_run):
        with pytest.raises(ToolchainExecutionError) as exc_info:
            ObjWriter().emit(ir, tmp_path / "out.o", config)
    assert "objcopy-that-does-not-exist" in str(exc_info.value)
    assert not (source.parent / "tmp" / "obj_writer_scratch").exists()  # cleaned up here too


def test_successful_emit_writes_input_and_calls_objcopy(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrelevant")
    ir = TableIR(name="sensor temp!", data=b"\xde\xad\xbe\xef", source_path=source, source_format="c_source")
    config = {"plugin": {"obj": {"objcopy": "my_objcopy", "objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}}
    out_path = tmp_path / "out.o"

    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        bin_path = Path(cmd[-2])
        captured["bin_content"] = bin_path.read_bytes()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(_obj_writer_module.subprocess, "run", fake_run):
        result = ObjWriter().emit(ir, out_path, config)

    assert result == out_path
    assert captured["bin_content"] == b"\xde\xad\xbe\xef"
    assert captured["cmd"][0] == "my_objcopy"
    assert "-I" in captured["cmd"] and "binary" in captured["cmd"]
    assert "--rename-section" in captured["cmd"]
    assert any(f".data={section_name_for('sensor temp!')}" in part for part in captured["cmd"])
    assert not (source.parent / "tmp" / "obj_writer_scratch").exists()  # cleaned up after emit


def test_scratch_dir_cleaned_up_even_on_failure(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrelevant")
    ir = TableIR(name="sensor", data=b"\x0a", source_path=source, source_format="c_source")
    config = {"plugin": {"obj": {"objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}}

    def fake_run(cmd, capture_output=True, text=True):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")

    with patch.object(_obj_writer_module.subprocess, "run", fake_run):
        with pytest.raises(ToolchainExecutionError):
            ObjWriter().emit(ir, tmp_path / "out.o", config)

    assert not (source.parent / "tmp" / "obj_writer_scratch").exists()


def test_section_name_for_sanitizes_and_prefixes():
    assert section_name_for("sensor temp!") == "table_sensor_temp_"
    assert section_name_for("3volt") == "table_3volt"
