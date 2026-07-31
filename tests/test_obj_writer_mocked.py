"""
Test di obj_writer con subprocess MOCKATO — vedi
test_c_source_reader_mocked.py per la stessa motivazione: coprire ogni
ramo senza dipendere da un objcopy reale installato sulla macchina che
esegue i test (test_c_source_and_obj.py resta l'integrazione con
toolchain vero, saltata se assente)."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.errors import ToolchainExecutionError, WriterEmitError
from payload.core.ir import TableIR
from payload.writers.obj_writer import ObjWriter, section_name_for


def _ir(name="sensor", data=b"\x0a\x1b\x2c\x3d") -> TableIR:
    return TableIR(name=name, data=data, source_path=Path("sensor.c"), source_format="c_source")


def test_missing_objcopy_target_raises(tmp_path):
    with pytest.raises(WriterEmitError):
        ObjWriter().emit(_ir(), tmp_path / "out.o", {"toolchain": {}})


def test_missing_objcopy_arch_raises(tmp_path):
    with pytest.raises(WriterEmitError):
        ObjWriter().emit(_ir(), tmp_path / "out.o", {"toolchain": {"objcopy_target": "elf32-littlearm"}})


def test_objcopy_failure_raises_toolchain_error(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrilevante")
    ir = TableIR(name="sensor", data=b"\x0a", source_path=source, source_format="c_source")
    config = {"toolchain": {"objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}

    def fake_run(cmd, capture_output=True, text=True):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="objcopy fallito")

    with patch("payload.writers.obj_writer.subprocess.run", fake_run):
        with pytest.raises(ToolchainExecutionError):
            ObjWriter().emit(ir, tmp_path / "out.o", config)


def test_successful_emit_writes_input_and_calls_objcopy(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrilevante")
    ir = TableIR(name="sensor temp!", data=b"\xde\xad\xbe\xef", source_path=source, source_format="c_source")
    config = {"toolchain": {"objcopy": "my_objcopy", "objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}
    out_path = tmp_path / "out.o"

    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        bin_path = Path(cmd[-2])
        captured["bin_content"] = bin_path.read_bytes()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("payload.writers.obj_writer.subprocess.run", fake_run):
        result = ObjWriter().emit(ir, out_path, config)

    assert result == out_path
    assert captured["bin_content"] == b"\xde\xad\xbe\xef"
    assert captured["cmd"][0] == "my_objcopy"
    assert "-I" in captured["cmd"] and "binary" in captured["cmd"]
    assert "--rename-section" in captured["cmd"]
    assert any(f".data={section_name_for('sensor temp!')}" in part for part in captured["cmd"])
    assert not (source.parent / "tmp" / "obj_writer_scratch").exists()  # ripulita dopo l'emit


def test_scratch_dir_cleaned_up_even_on_failure(tmp_path):
    source = tmp_path / "sensor.c"
    source.write_text("irrilevante")
    ir = TableIR(name="sensor", data=b"\x0a", source_path=source, source_format="c_source")
    config = {"toolchain": {"objcopy_target": "elf32-littlearm", "objcopy_arch": "arm"}}

    def fake_run(cmd, capture_output=True, text=True):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fallito")

    with patch("payload.writers.obj_writer.subprocess.run", fake_run):
        with pytest.raises(ToolchainExecutionError):
            ObjWriter().emit(ir, tmp_path / "out.o", config)

    assert not (source.parent / "tmp" / "obj_writer_scratch").exists()


def test_section_name_for_sanitizes_and_prefixes():
    assert section_name_for("sensor temp!") == "table_sensor_temp_"
    assert section_name_for("3volt") == "table_3volt"
