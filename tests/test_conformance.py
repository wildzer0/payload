from pathlib import Path

import pytest

from payload.core.ir import TableIR
from payload.testing import assert_reader_conforms, assert_writer_conforms
from tests.example_plugins_helper import load_example_plugin

CsvReader = load_example_plugin("csv_reader.py").CsvReader
RawTextReader = load_example_plugin("raw_text.py").RawTextReader
BinWriter = load_example_plugin("bin_writer.py").BinWriter
HeaderWriter = load_example_plugin("header_writer.py").HeaderWriter
HexWriter = load_example_plugin("hex_writer.py").HexWriter


# The example plugins must always pass their own conformance suite —
# if one of these fails, it's a regression in the core.

def test_raw_text_reader_conforms(tmp_path):
    sample = tmp_path / "t.raw"
    sample.write_text("0x0A, 0x1B\n")
    assert_reader_conforms(RawTextReader(), sample)


def test_csv_reader_conforms(tmp_path):
    sample = tmp_path / "t.csv"
    sample.write_text("value\n0x0A\n")
    assert_reader_conforms(CsvReader(), sample)


def test_bin_writer_conforms(tmp_path):
    ir = TableIR(name="t", data=b"\x00\x01", source_path=Path("x"), source_format="fake")
    assert_writer_conforms(BinWriter(), ir, tmp_path)


def test_hex_writer_conforms(tmp_path):
    ir = TableIR(name="t", data=b"\x00\x01", source_path=Path("x"), source_format="fake")
    assert_writer_conforms(HexWriter(), ir, tmp_path)


def test_header_writer_conforms(tmp_path):
    ir = TableIR(name="t", data=b"\x00\x01", source_path=Path("x"), source_format="fake")
    assert_writer_conforms(HeaderWriter(), ir, tmp_path)


# The suite must also be able to CATCH real violations, not just
# validate already-correct plugins — otherwise it would be worthless.

class _ReturnsWrongType:
    name = "bad"
    extensions = [".bad"]
    api_version = "1.0"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        return {"not": "a TableIR"}


def test_conformance_catches_wrong_return_type(tmp_path):
    sample = tmp_path / "t.bad"
    sample.write_text("x")
    with pytest.raises(AssertionError):
        assert_reader_conforms(_ReturnsWrongType(), sample)


class _MissingAttributes:
    name = ""  # empty, invalid
    api_version = "1.0"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        raise NotImplementedError


def test_conformance_catches_missing_name(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    with pytest.raises(AssertionError):
        assert_reader_conforms(_MissingAttributes(), sample)


class _RaisesGenericException:
    name = "broken"
    extensions = [".broken"]
    api_version = "1.0"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        raise ValueError("shouldn't raise this")


def test_conformance_catches_generic_exception_instead_of_payload_error(tmp_path):
    sample = tmp_path / "t.broken"
    sample.write_text("x")
    with pytest.raises(AssertionError):
        assert_reader_conforms(_RaisesGenericException(), sample)
