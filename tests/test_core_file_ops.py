"""Core file operations (payload/core/file_ops.py): byte-level compare,
content search, and binary analysis — shared by the CLI and the web."""
from pathlib import Path

import pytest

from payload.core.file_ops import analyze_file, compare_files, search_files


def _proj(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    return root


def _file(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---------- compare ----------

def test_compare_identical(tmp_path):
    a = _file(tmp_path, "a.bin", b"hello world")
    b = _file(tmp_path, "b.bin", b"hello world")
    r = compare_files(a, b)
    assert r["equal"] is True
    assert r["a_size"] == r["b_size"] == 11
    assert r["runs"] == []


def test_compare_differing_runs(tmp_path):
    a = _file(tmp_path, "a.bin", b"hello world")
    b = _file(tmp_path, "b.bin", b"hello Xorld")
    r = compare_files(a, b)
    assert r["equal"] is False
    assert r["prefix"] == 6
    assert r["runs"] == [{"offset": 6, "length": 1}]


def test_compare_size_tail_is_reported(tmp_path):
    a = _file(tmp_path, "a.bin", b"abc")
    b = _file(tmp_path, "b.bin", b"abcXY")
    r = compare_files(a, b)
    assert r["a_size"] == 3
    assert r["b_size"] == 5
    assert {"offset": 3, "length": 2, "file": "b"} in r["runs"]


def test_compare_empty_files(tmp_path):
    a = _file(tmp_path, "a.bin", b"")
    b = _file(tmp_path, "b.bin", b"")
    assert compare_files(a, b)["equal"] is True


def test_compare_truncated(monkeypatch, tmp_path):
    import payload.core.file_ops as fo
    monkeypatch.setattr(fo, "READ_CAP", 4)
    a = _file(tmp_path, "a.bin", b"0123456789")
    b = _file(tmp_path, "b.bin", b"0123456789")
    r = compare_files(a, b)
    assert r["equal"] is True
    assert r["truncated"] is True


# ---------- search ----------

def test_search_text(tmp_path):
    root = _proj(tmp_path)
    (root / "a.raw").write_text("value 0x0A here\n")
    (root / "b.raw").write_text("nothing here\n")
    r = search_files(root, b"0x0A")
    assert len(r["matches"]) == 1
    assert r["matches"][0]["path"] == "a.raw"
    assert r["matches"][0]["hex"] == "30 78 30 41"  # "0x0A"
    assert r["searched"] == 3  # a.raw + b.raw + table-tool.toml


def test_search_skips_internal(tmp_path):
    root = _proj(tmp_path)
    (root / "build").mkdir()
    (root / "build" / "out.bin").write_bytes(b"0x0A")
    (root / ".hidden.raw").write_text("0x0A")
    (root / "a.raw").write_text("0x0A")
    r = search_files(root, b"0x0A")
    assert [m["path"] for m in r["matches"]] == ["a.raw"]  # build/, .hidden skipped
    assert r["searched"] == 2  # a.raw + table-tool.toml


def test_search_hex_pattern(tmp_path):
    root = _proj(tmp_path)
    (root / "fw.bin").write_bytes(b"\x00\x0a\x1b\x00")
    r = search_files(root, b"\x0a\x1b")
    assert r["matches"][0]["offset"] == 1
    assert r["matches"][0]["hex"] == "0A 1B"


def test_search_truncates_results(tmp_path):
    root = _proj(tmp_path)
    (root / "a.raw").write_bytes(b"x" * 100)
    r = search_files(root, b"x", max_results=10)
    assert len(r["matches"]) == 10
    assert r["truncated"] is True


def test_search_start_subdir(tmp_path):
    root = _proj(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "a.raw").write_text("needle")
    (root / "top.raw").write_text("needle")
    r = search_files(root, b"needle", start=root / "sub")
    assert [m["path"] for m in r["matches"]] == ["sub/a.raw"]


# ---------- analyze ----------

def test_analyze_empty(tmp_path):
    p = _file(tmp_path, "empty.bin", b"")
    r = analyze_file(p)
    assert r["entropy"] == 0.0
    assert r["freq"] == []
    assert r["magic"] == []


def test_analyze_text_low_entropy_high_printable(tmp_path):
    p = _file(tmp_path, "t.txt", b"aaaa bbbb cccc dddd\n" * 10)
    r = analyze_file(p)
    assert r["printable_ratio"] > 0.9
    assert r["entropy"] < 4
    assert r["ascii_runs"] >= 1
    assert 0 < r["distinct"] <= 256
    assert r["null_ratio"] >= 0.0


def test_analyze_elf_magic(tmp_path):
    p = _file(tmp_path, "a.elf", b"\x7fELF\x02\x01\x01" + b"\x00" * 20)
    r = analyze_file(p)
    assert "ELF executable" in r["magic"]


def test_analyze_freq_and_capped(monkeypatch, tmp_path):
    import payload.core.file_ops as fo
    monkeypatch.setattr(fo, "READ_CAP", 4)
    p = _file(tmp_path, "big.bin", b"\x01\x01\x01\x02\x03\x04")
    r = analyze_file(p)
    assert r["capped"] is True
    assert r["analyzed"] == 4
    freq = dict(r["freq"])
    assert freq[0x01] == 3


def test_compare_midfile_run_closes_before_suffix(tmp_path):
    # two separate runs: the first one closes mid-file (line 69-71), the
    # second one at the loop end
    a = _file(tmp_path, "a.bin", b"ABXCDXEF")
    b = _file(tmp_path, "b.bin", b"ABYCDYEF")
    r = compare_files(a, b)
    assert r["prefix"] == 2
    assert r["runs"] == [{"offset": 2, "length": 1}, {"offset": 5, "length": 1}]


def test_compare_extra_tail_in_a(tmp_path):
    a = _file(tmp_path, "a.bin", b"abcXY")
    b = _file(tmp_path, "b.bin", b"abc")
    r = compare_files(a, b)
    assert {"offset": 3, "length": 2, "file": "a"} in r["runs"]


def test_magic_all_signatures(tmp_path):
    cases = {
        "png.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
        "img.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 8,
        "a.zip": b"PK\x03\x04" + b"\x00" * 8,
        "a.gz": b"\x1f\x8b\x08" + b"\x00" * 8,
        "a.pdf": b"%PDF-1.7" + b"\x00" * 8,
        "a.bmp": b"BM\x00\x00" + b"\x00" * 8,
        "s.sh": b"#!/bin/sh\n",
    }
    for name, data in cases.items():
        r = analyze_file(_file(tmp_path, name, data))
        assert r["magic"], name


def test_analyze_trailing_ascii_run(tmp_path):
    p = _file(tmp_path, "t.txt", b"\x00abcde")  # printable run ends at EOF
    r = analyze_file(p)
    assert r["ascii_runs"] == 1


def test_search_survives_invalid_config(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "table-tool.toml").write_text("[defaults\n")  # malformed: load_config raises
    (root / "build").mkdir()
    (root / "build" / "out.bin").write_bytes(b"needle")
    (root / "a.raw").write_text("needle")
    r = search_files(root, b"needle")
    # fallback exclusions still skip build/; the search itself must not crash
    assert [m["path"] for m in r["matches"]] == ["a.raw"]
