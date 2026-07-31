"""
Test della suite di conformità stessa (payload/testing.py) — deve
saper intercettare OGNI violazione del contratto Reader/Writer
descritto in docs/PLUGINS.md, non solo i casi già coperti in
test_conformance.py. Ogni fake qui sotto viola esattamente UNA regola,
per isolare quale check la intercetta."""
from pathlib import Path

import pytest

from payload.core.errors import PayloadError, ReaderParseError, WriterEmitError
from payload.core.ir import TableIR
from payload.testing import (
    assert_reader_conforms,
    assert_writer_conforms,
    check_reader_behavior,
    check_reader_structure,
    check_writer_behavior,
    check_writer_structure,
)


def _issue_checks(issues):
    return {i.check for i in issues}


# --- check_reader_structure -----------------------------------------------

class _NoApiVersion:
    name = "x"
    extensions = [".x"]
    def sniff(self, path): return False
    def parse(self, path, config): raise NotImplementedError


def test_missing_api_version_flagged():
    assert "api_version" in _issue_checks(check_reader_structure(_NoApiVersion()))


class _ExtensionWithoutDot:
    name = "x"
    api_version = "1.0"
    extensions = ["x"]  # manca il punto iniziale
    def sniff(self, path): return False
    def parse(self, path, config): raise NotImplementedError


def test_extension_without_dot_flagged():
    assert "extensions" in _issue_checks(check_reader_structure(_ExtensionWithoutDot()))


class _NoParseMethod:
    name = "x"
    api_version = "1.0"
    extensions = [".x"]
    def sniff(self, path): return False


def test_missing_parse_method_flagged():
    assert "parse" in _issue_checks(check_reader_structure(_NoParseMethod()))


class _NoSniffMethod:
    name = "x"
    api_version = "1.0"
    extensions = [".x"]
    def parse(self, path, config): raise NotImplementedError


def test_missing_sniff_method_flagged():
    assert "sniff" in _issue_checks(check_reader_structure(_NoSniffMethod()))


# --- check_reader_behavior --------------------------------------------------

class _RaisesReaderParseError:
    name = "x"
    def sniff(self, path): return False
    def parse(self, path, config):
        raise ReaderParseError(path, "fallisce sempre, anche su un sample valido")


def test_reader_raising_payload_error_flagged(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    assert "parse" in _issue_checks(check_reader_behavior(_RaisesReaderParseError(), sample))


class _ReturnsNonBytesData:
    name = "x"
    def sniff(self, path): return False
    def parse(self, path, config):
        return TableIR(name="t", data="non bytes", source_path=path, source_format=self.name)


def test_reader_non_bytes_data_flagged(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    assert "data" in _issue_checks(check_reader_behavior(_ReturnsNonBytesData(), sample))


class _ReturnsEmptyName:
    name = "x"
    def sniff(self, path): return False
    def parse(self, path, config):
        return TableIR(name="", data=b"x", source_path=path, source_format=self.name)


def test_reader_empty_ir_name_flagged(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    assert "name" in _issue_checks(check_reader_behavior(_ReturnsEmptyName(), sample))


class _WrongSourceFormat:
    name = "x"
    def sniff(self, path): return False
    def parse(self, path, config):
        return TableIR(name="t", data=b"x", source_path=path, source_format="qualcos_altro")


def test_reader_wrong_source_format_flagged(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    assert "source_format" in _issue_checks(check_reader_behavior(_WrongSourceFormat(), sample))


class _WrongSourcePath:
    name = "x"
    def sniff(self, path): return False
    def parse(self, path, config):
        return TableIR(name="t", data=b"x", source_path=Path("/percorso/diverso"), source_format=self.name)


def test_reader_wrong_source_path_flagged(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    assert "source_path" in _issue_checks(check_reader_behavior(_WrongSourcePath(), sample))


def test_assert_reader_conforms_raises_with_details(tmp_path):
    sample = tmp_path / "t.x"
    sample.write_text("x")
    with pytest.raises(AssertionError, match="non è conforme"):
        assert_reader_conforms(_NoApiVersion(), sample)


# --- check_writer_structure -------------------------------------------------

class _WriterNoName:
    api_version = "1.0"
    extension = ".x"
    def emit(self, ir, out_path, config): return out_path


def test_writer_missing_name_flagged():
    assert "name" in _issue_checks(check_writer_structure(_WriterNoName()))


class _WriterNoApiVersion:
    name = "x"
    extension = ".x"
    def emit(self, ir, out_path, config): return out_path


def test_writer_missing_api_version_flagged():
    assert "api_version" in _issue_checks(check_writer_structure(_WriterNoApiVersion()))


class _WriterBadExtension:
    name = "x"
    api_version = "1.0"
    extension = "x"  # manca il punto iniziale
    def emit(self, ir, out_path, config): return out_path


def test_writer_bad_extension_flagged():
    assert "extension" in _issue_checks(check_writer_structure(_WriterBadExtension()))


class _WriterNoEmit:
    name = "x"
    api_version = "1.0"
    extension = ".x"


def test_writer_missing_emit_flagged():
    assert "emit" in _issue_checks(check_writer_structure(_WriterNoEmit()))


# --- check_writer_behavior ---------------------------------------------------

def _sample_ir():
    return TableIR(name="t", data=b"\x00\x01", source_path=Path("x"), source_format="fake")


class _WriterRaisesPayloadError:
    name = "x"
    extension = ".x"
    def emit(self, ir, out_path, config):
        raise WriterEmitError(self.name, "fallisce sempre")


def test_writer_raising_payload_error_flagged(tmp_path):
    assert "emit" in _issue_checks(check_writer_behavior(_WriterRaisesPayloadError(), _sample_ir(), tmp_path))


class _WriterRaisesGenericException:
    name = "x"
    extension = ".x"
    def emit(self, ir, out_path, config):
        raise ValueError("non dovrei sollevare questo")


def test_writer_raising_generic_exception_flagged(tmp_path):
    assert "emit" in _issue_checks(check_writer_behavior(_WriterRaisesGenericException(), _sample_ir(), tmp_path))


class _WriterReturnsNonPath:
    name = "x"
    extension = ".x"
    def emit(self, ir, out_path, config):
        return "non un Path"


def test_writer_returning_non_path_flagged(tmp_path):
    assert "emit" in _issue_checks(check_writer_behavior(_WriterReturnsNonPath(), _sample_ir(), tmp_path))


class _WriterDoesNotWriteFile:
    name = "x"
    extension = ".x"
    def emit(self, ir, out_path, config):
        return out_path  # dichiara questo path ma non lo scrive mai


def test_writer_declared_file_missing_flagged(tmp_path):
    assert "emit" in _issue_checks(check_writer_behavior(_WriterDoesNotWriteFile(), _sample_ir(), tmp_path))


def test_assert_writer_conforms_raises_with_details(tmp_path):
    with pytest.raises(AssertionError, match="non è conforme"):
        assert_writer_conforms(_WriterDoesNotWriteFile(), _sample_ir(), tmp_path)
