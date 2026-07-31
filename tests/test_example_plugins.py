from pathlib import Path

import pytest

from payload.core.errors import ReaderParseError, WriterEmitError
from payload.core.ir import TableIR
from payload.readers.csv_reader import CsvReader
from payload.writers.hex_writer import HexWriter


# --- CsvReader ---------------------------------------------------------

def test_csv_reader_parses_values_and_comments(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,comment\n0x0A,soglia min\n0x1B,\n0x2C,soglia max\n")

    ir = CsvReader().parse(csv_file, {})

    assert ir.data == bytes([0x0A, 0x1B, 0x2C])
    assert ir.comments == [(0, "soglia min"), (2, "soglia max")]


def test_csv_reader_accepts_decimal_values(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value\n10\n255\n")
    ir = CsvReader().parse(csv_file, {})
    assert ir.data == bytes([10, 255])


def test_csv_reader_missing_column_raises(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("foo,bar\n1,2\n")
    with pytest.raises(ReaderParseError):
        CsvReader().parse(csv_file, {})


def test_csv_reader_out_of_range_raises(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value\n0x1FF\n")
    with pytest.raises(ReaderParseError):
        CsvReader().parse(csv_file, {})


def test_csv_reader_explicit_offset_fills_gap(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("offset,value\n0,0xAA\n3,0xBB\n")
    ir = CsvReader().parse(csv_file, {})
    assert ir.data == bytes([0xAA, 0x00, 0x00, 0xBB])


def test_csv_reader_sniff_detects_value_header(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,comment\n0x01,\n")
    assert CsvReader().sniff(csv_file) is True


# --- HexWriter -----------------------------------------------------------

def _ir(data: bytes) -> TableIR:
    return TableIR(name="t", data=data, source_path=Path("x"), source_format="fake")


def test_hex_writer_produces_valid_checksum(tmp_path):
    out = tmp_path / "t.hex"
    HexWriter().emit(_ir(bytes([0x0A, 0x1B, 0x2C, 0x3D, 0xFF])), out, {})

    lines = out.read_text().splitlines()
    assert lines[0] == ":050000000A1B2C3DFF6E"
    assert lines[-1] == ":00000001FF"


def test_hex_writer_splits_across_multiple_lines(tmp_path):
    out = tmp_path / "t.hex"
    HexWriter().emit(_ir(bytes(range(20))), out, {})  # 20 bytes > 16 per riga

    lines = out.read_text().splitlines()
    assert len(lines) == 3  # 16 bytes + 4 bytes + EOF
    assert lines[-1] == ":00000001FF"


def test_hex_writer_rejects_oversized_table(tmp_path):
    out = tmp_path / "t.hex"
    with pytest.raises(WriterEmitError):
        HexWriter().emit(_ir(bytes(0x10000)), out, {})  # 65536 bytes, oltre il limite
