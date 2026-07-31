from pathlib import Path

import pytest

from payload.core.ir import TableIR
from payload.readers.csv_reader import CsvReader
from payload.readers.raw_text import RawTextReader
from payload.writers.bin_writer import BinWriter
from payload.writers.header_writer import HeaderWriter
from payload.writers.hex_writer import HexWriter
from payload.testing import assert_reader_conforms, assert_writer_conforms


# I plugin builtin devono sempre passare la loro stessa suite di
# conformità — se uno di questi fallisce, è una regressione nel core.

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


# La suite deve anche saper INTERCETTARE violazioni vere, non solo
# validare plugin già corretti — altrimenti non varrebbe niente.

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
    name = ""  # vuoto, non valido
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
        raise ValueError("non dovrei sollevare questo")


def test_conformance_catches_generic_exception_instead_of_payload_error(tmp_path):
    sample = tmp_path / "t.broken"
    sample.write_text("x")
    with pytest.raises(AssertionError):
        assert_reader_conforms(_RaisesGenericException(), sample)
