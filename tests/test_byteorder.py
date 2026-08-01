from pathlib import Path

import pytest

from payload.core.byteorder import pack_value, repack, unpack_value
from payload.readers.csv_reader import CsvReader
from payload.writers.bin_writer import BinWriter


# --- byteorder helpers ---------------------------------------------------

def test_pack_value_little_endian():
    assert pack_value(0x1234, width=2, byte_order="little") == bytes.fromhex("3412")


def test_pack_value_invalid_byte_order_raises():
    with pytest.raises(ValueError, match="little.*big"):
        pack_value(1, width=1, byte_order="middle")


def test_pack_value_unsupported_width_raises():
    with pytest.raises(ValueError, match="unsupported width"):
        pack_value(1, width=3, byte_order="little")


def test_repack_non_contiguous_offset_raises():
    fields = [
        {"offset": 0, "width": 1, "value": 0x01},
        {"offset": 5, "width": 1, "value": 0x02},  # gap between offset 1 and 5
    ]
    with pytest.raises(ValueError, match="contiguous"):
        repack(fields, "little")


def test_pack_value_big_endian():
    assert pack_value(0x1234, width=2, byte_order="big") == bytes.fromhex("1234")


def test_pack_unpack_roundtrip():
    for order in ("little", "big"):
        for width in (1, 2, 4, 8):
            value = (1 << (width * 8)) - 1  # max value for that width
            packed = pack_value(value, width, order)
            assert unpack_value(packed, width, order) == value


def test_repack_multiple_fields_contiguous():
    fields = [
        {"offset": 0, "width": 2, "value": 0x1234},
        {"offset": 2, "width": 4, "value": 0xDEADBEEF},
    ]
    le = repack(fields, "little")
    be = repack(fields, "big")
    assert le == bytes.fromhex("3412efbeadde")
    assert be == bytes.fromhex("1234deadbeef")


def test_repack_empty_fields_returns_empty_bytes():
    assert repack([], "little") == b""


# --- csv_reader + bin_writer integration -----------------------------------

def test_csv_reader_respects_configured_byte_order(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,width\n0x1234,2\n")

    ir_le = CsvReader().parse(csv_file, {"defaults": {"byte_order": "little"}})
    ir_be = CsvReader().parse(csv_file, {"defaults": {"byte_order": "big"}})

    assert ir_le.data == bytes.fromhex("3412")
    assert ir_be.data == bytes.fromhex("1234")
    assert ir_le.byte_order == "little"
    assert ir_be.byte_order == "big"


def test_csv_reader_populates_structured_fields(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,width\n0x1234,2\n0xAB,1\n")

    ir = CsvReader().parse(csv_file, {})
    assert ir.extra["fields"] == [
        {"offset": 0, "width": 2, "value": 0x1234},
        {"offset": 2, "width": 1, "value": 0xAB},
    ]


def test_bin_writer_repacks_when_target_order_differs(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,width\n0x1234,2\n0xDEADBEEF,4\n")

    ir = CsvReader().parse(csv_file, {"defaults": {"byte_order": "little"}})
    out = tmp_path / "out.bin"
    BinWriter().emit(ir, out, {"defaults": {"byte_order": "big"}})

    assert out.read_bytes() == bytes.fromhex("1234deadbeef")


def test_bin_writer_passthrough_when_same_order(tmp_path):
    csv_file = tmp_path / "t.csv"
    csv_file.write_text("value,width\n0x1234,2\n")

    ir = CsvReader().parse(csv_file, {"defaults": {"byte_order": "little"}})
    out = tmp_path / "out.bin"
    BinWriter().emit(ir, out, {"defaults": {"byte_order": "little"}})

    assert out.read_bytes() == ir.data


def test_bin_writer_falls_back_without_structured_fields(tmp_path):
    # a reader without extra['fields'] (e.g. raw_text): the writer can't
    # blindly reinterpret, it must just pass through the bytes received
    from payload.readers.raw_text import RawTextReader

    raw_file = tmp_path / "t.raw"
    raw_file.write_text("0x0A, 0x1B\n")
    ir = RawTextReader().parse(raw_file, {})

    out = tmp_path / "out.bin"
    BinWriter().emit(ir, out, {"defaults": {"byte_order": "big"}})

    assert out.read_bytes() == bytes([0x0A, 0x1B])  # unchanged, no crash
