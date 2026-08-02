from pathlib import Path

import pytest

from payload.core.errors import ReaderParseError, WriterEmitError
from payload.core.ir import TableIR
from tests.example_plugins_helper import load_example_plugin

CsvReader = load_example_plugin("csv_reader.py").CsvReader
HexWriter = load_example_plugin("hex_writer.py").HexWriter


# --- CsvReader ---------------------------------------------------------

def test_csv_reader_parses_values_and_comments(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,comment\n0x0A,min threshold\n0x1B,\n0x2C,max threshold\n")

    ir = CsvReader().parse(csv_file, {})

    assert ir.data == bytes([0x0A, 0x1B, 0x2C])
    assert ir.comments == [(0, "min threshold"), (2, "max threshold")]


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


def test_csv_reader_sniff_returns_false_on_unreadable_path(tmp_path):
    directory = tmp_path / "a_folder"
    directory.mkdir()
    assert CsvReader().sniff(directory) is False


def test_csv_reader_skips_rows_with_empty_value(tmp_path):
    csv_file = tmp_path / "t.csv"
    # a "truly" empty row (no commas) gets discarded by the csv module
    # itself, before it even reaches the reader — here instead the row
    # exists with an empty 'value' column, to exercise the reader's
    # explicit skip
    csv_file.write_text("value,comment\n0x0A,x\n,\n0x0B,y\n")
    ir = CsvReader().parse(csv_file, {})
    assert ir.data == bytes([0x0A, 0x0B])


def test_csv_reader_invalid_value_raises(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value\nnot_a_number\n")
    with pytest.raises(ReaderParseError):
        CsvReader().parse(csv_file, {})


def test_csv_reader_unsupported_width_raises(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,width\n0x0A,3\n")
    with pytest.raises(ReaderParseError):
        CsvReader().parse(csv_file, {})


def test_csv_reader_overlapping_offset_raises(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("offset,value\n0,0xAA\n2,0xBB\n1,0xCC\n")
    with pytest.raises(ReaderParseError):
        CsvReader().parse(csv_file, {})


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
    HexWriter().emit(_ir(bytes(range(20))), out, {})  # 20 bytes > 16 per line

    lines = out.read_text().splitlines()
    assert len(lines) == 3  # 16 bytes + 4 bytes + EOF
    assert lines[-1] == ":00000001FF"


def test_hex_writer_rejects_oversized_table(tmp_path):
    out = tmp_path / "t.hex"
    with pytest.raises(WriterEmitError):
        HexWriter().emit(_ir(bytes(0x10000)), out, {})  # 65536 bytes, over the limit
