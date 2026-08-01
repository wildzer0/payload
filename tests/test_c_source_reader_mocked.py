"""
Test del reader c_source con subprocess MOCKATO: a differenza di
test_c_source_and_obj.py (che richiede gcc/objcopy reali e si salta se
assenti), questi coprono ogni ramo del reader in modo deterministico e
portabile, indipendentemente dal toolchain installato sulla macchina
che esegue i test.
"""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.errors import ReaderParseError, ToolchainExecutionError
from payload.readers.c_source import CSourceReader

C_SOURCE = '''#include <stdint.h>

const uint8_t table_data[] __attribute__((section("payload_table_data"))) = {
    0x0A, 0x1B,  // soglia min
    0x2C, 0x3D,  // soglia max
};
'''


def _fake_run(compile_rc=0, extract_rc=0, bin_content: bytes | None = b"", delete_source_before_extract=False, source_path=None):
    def _run(cmd, capture_output=True, text=True):
        if "-c" in cmd:
            return subprocess.CompletedProcess(cmd, compile_rc, stdout="", stderr="errore compilazione" if compile_rc else "")
        if delete_source_before_extract and source_path is not None:
            source_path.unlink()
        if extract_rc == 0 and bin_content is not None:
            Path(cmd[-1]).write_bytes(bin_content)
        return subprocess.CompletedProcess(cmd, extract_rc, stdout="", stderr="errore estrazione" if extract_rc else "")
    return _run


def test_sniff_always_false(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    assert CSourceReader().sniff(c_file) is False


def test_compile_failure_raises_toolchain_error(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(compile_rc=1)):
        with pytest.raises(ToolchainExecutionError):
            CSourceReader().parse(c_file, {})


def test_extract_failure_raises_toolchain_error(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(extract_rc=1)):
        with pytest.raises(ToolchainExecutionError):
            CSourceReader().parse(c_file, {})


def test_compiler_not_found_raises_toolchain_error_not_bare_exception(tmp_path):
    """subprocess.run() solleva FileNotFoundError (non un returncode
    diverso da zero) quando l'eseguibile non esiste proprio — prima
    non era gestito, quindi arrivava all'utente come traceback Python
    grezzo invece di un ToolchainExecutionError pulito."""
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", side_effect=FileNotFoundError(2, "No such file or directory", "gcc-che-non-esiste")):
        with pytest.raises(ToolchainExecutionError) as exc_info:
            CSourceReader().parse(c_file, {"toolchain": {"compiler": "gcc-che-non-esiste"}})
    assert "gcc-che-non-esiste" in str(exc_info.value)


def test_objcopy_not_found_raises_toolchain_error_not_bare_exception(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)

    def _run(cmd, capture_output=True, text=True):
        if "-c" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise FileNotFoundError(2, "No such file or directory", "objcopy-che-non-esiste")

    with patch("payload.readers.c_source.subprocess.run", _run):
        with pytest.raises(ToolchainExecutionError) as exc_info:
            CSourceReader().parse(c_file, {"toolchain": {"objcopy": "objcopy-che-non-esiste"}})
    assert "objcopy-che-non-esiste" in str(exc_info.value)


def test_no_section_extracted_raises_reader_parse_error(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(bin_content=None)):
        with pytest.raises(ReaderParseError):
            CSourceReader().parse(c_file, {})


def test_empty_section_raises_reader_parse_error(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(bin_content=b"")):
        with pytest.raises(ReaderParseError):
            CSourceReader().parse(c_file, {})


def test_successful_extraction_returns_data_and_comments(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(bin_content=bytes([0x0A, 0x1B, 0x2C, 0x3D]))):
        ir = CSourceReader().parse(c_file, {"toolchain": {"compiler": "gcc", "objcopy": "objcopy"}})

    assert ir.data == bytes([0x0A, 0x1B, 0x2C, 0x3D])
    assert ir.comments == [(0, "soglia min"), (2, "soglia max")]
    assert ir.source_format == "c_source"


def test_comment_extraction_skipped_when_byte_count_mismatches(tmp_path):
    """Se il conteggio di 0x... nel sorgente non torna con i bytes
    realmente compilati (parsing testuale best-effort inaffidabile),
    i commenti vengono scartati invece di essere associati agli offset
    sbagliati."""
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)  # dichiara 4 valori 0x.. nel sorgente
    with patch("payload.readers.c_source.subprocess.run", _fake_run(bin_content=bytes([0x0A, 0x1B]))):  # ma solo 2 bytes compilati
        ir = CSourceReader().parse(c_file, {})

    assert ir.data == bytes([0x0A, 0x1B])
    assert ir.comments == []


def test_comment_extraction_tolerates_source_disappearing(tmp_path):
    """_extract_comments_best_effort rilegge il sorgente DOPO la
    compilazione: se nel frattempo non è più leggibile, i commenti
    vengono semplicemente omessi (mai un errore) — i bytes compilati
    restano comunque validi."""
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    fake = _fake_run(bin_content=bytes([0x0A, 0x1B, 0x2C, 0x3D]), delete_source_before_extract=True, source_path=c_file)
    with patch("payload.readers.c_source.subprocess.run", fake):
        ir = CSourceReader().parse(c_file, {})

    assert ir.data == bytes([0x0A, 0x1B, 0x2C, 0x3D])
    assert ir.comments == []


def test_scratch_dir_cleaned_up_even_on_failure(tmp_path):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE)
    with patch("payload.readers.c_source.subprocess.run", _fake_run(compile_rc=1)):
        with pytest.raises(ToolchainExecutionError):
            CSourceReader().parse(c_file, {})

    assert not (tmp_path / "tmp" / "c_source_scratch").exists()
