import pytest

from payload.core.errors import ReaderParseError
from tests.example_plugins_helper import load_example_plugin

RawTextReader = load_example_plugin("raw_text.py").RawTextReader


def test_sniff_detects_hex_values(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("0x0A, 0x1B\n")
    assert RawTextReader().sniff(p) is True


def test_sniff_false_without_hex_marker(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("no value here\n")
    assert RawTextReader().sniff(p) is False


def test_sniff_returns_false_on_unreadable_path(tmp_path):
    # a directory instead of a file -> read_text() raises OSError
    directory = tmp_path / "a_folder"
    directory.mkdir()
    assert RawTextReader().sniff(directory) is False


def test_parse_ignores_full_line_comments(tmp_path):
    p = tmp_path / "t.raw"
    p.write_text("# just a comment\n0x0A\n")
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
    p.write_text("0x0A, 0x1B  # threshold\n")
    ir = RawTextReader().parse(p, {})
    assert ir.comments == [(0, "threshold")]


# --- parse_many (batch tables, see src/payload/docs/BATCH.md) --------------


def test_parse_many_concatenates_files_in_given_order(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("0x0A\n")
    p2.write_text("0x1B\n")
    ir = RawTextReader().parse_many([p1, p2], {})
    assert ir.data == bytes([0x0A, 0x1B])


def test_parse_many_respects_the_order_of_the_paths_list_not_filesystem_order(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("0x0A\n")
    p2.write_text("0x1B\n")
    ir = RawTextReader().parse_many([p2, p1], {})
    assert ir.data == bytes([0x1B, 0x0A])


def test_parse_many_comment_offsets_are_cumulative_across_files(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("0x0A, 0x1B\n")
    p2.write_text("0x2C  # second file\n")
    ir = RawTextReader().parse_many([p1, p2], {})
    assert ir.comments == [(2, "second file")]


def test_parse_many_name_and_source_path_come_from_first_file(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("0x0A\n")
    p2.write_text("0x1B\n")
    ir = RawTextReader().parse_many([p1, p2], {})
    assert ir.name == "ROW1"
    assert ir.source_path == p1


def test_parse_many_invalid_hex_value_raises_naming_the_offending_file(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("0x0A\n")
    p2.write_text("0xZZ\n")
    with pytest.raises(ReaderParseError, match="ROW2.txt"):
        RawTextReader().parse_many([p1, p2], {})


def test_parse_many_ignores_comments_and_empty_tokens_like_parse(tmp_path):
    p1 = tmp_path / "ROW1.txt"
    p2 = tmp_path / "ROW2.txt"
    p1.write_text("# just a comment\n0x0A,,0x1B,\n")
    p2.write_text("0x2C\n")
    ir = RawTextReader().parse_many([p1, p2], {})
    assert ir.data == bytes([0x0A, 0x1B, 0x2C])
