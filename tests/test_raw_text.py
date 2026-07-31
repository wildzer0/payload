import pytest

from payload.core.errors import ReaderParseError
from payload.readers.raw_text import RawTextReader


def test_sniff_detects_hex_values(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("0x0A, 0x1B\n")
    assert RawTextReader().sniff(p) is True


def test_sniff_false_without_hex_marker(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("nessun valore qui\n")
    assert RawTextReader().sniff(p) is False


def test_sniff_returns_false_on_unreadable_path(tmp_path):
    # una directory al posto di un file -> read_text() solleva OSError
    directory = tmp_path / "una_cartella"
    directory.mkdir()
    assert RawTextReader().sniff(directory) is False


def test_parse_ignores_full_line_comments(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("# solo un commento\n0x0A\n")
    ir = RawTextReader().parse(p, {})
    assert ir.data == bytes([0x0A])


def test_parse_skips_empty_tokens_between_commas(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("0x0A,,0x1B,\n")
    ir = RawTextReader().parse(p, {})
    assert ir.data == bytes([0x0A, 0x1B])


def test_parse_invalid_hex_value_raises(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("0xZZ\n")
    with pytest.raises(ReaderParseError):
        RawTextReader().parse(p, {})


def test_parse_attaches_trailing_comment(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("0x0A, 0x1B  # soglia\n")
    ir = RawTextReader().parse(p, {})
    assert ir.comments == [(0, "soglia")]
