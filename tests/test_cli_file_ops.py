"""CLI commands for the new file-level features: pld compare / grep /
analyze / activity — the CLI counterparts of the web file browser's
Compare / Search / Analyze and the Log page."""
from pathlib import Path

from typer.testing import CliRunner

from payload.cli import app
from payload.core.activity import log_event

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", name])
    assert result.exit_code == 0, result.stdout
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


# ---------- compare ----------

def test_compare_identical(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    a = Path("a.bin"); b = Path("b.bin")
    a.write_bytes(b"same data")
    b.write_bytes(b"same data")
    r = runner.invoke(app, ["compare", "a.bin", "b.bin"])
    assert r.exit_code == 0
    assert "Identical" in r.stdout


def test_compare_differing(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    Path("a.bin").write_bytes(b"hello world")
    Path("b.bin").write_bytes(b"hello Xorld")
    r = runner.invoke(app, ["compare", "a.bin", "b.bin"])
    assert r.exit_code == 0
    assert "differing run" in r.stdout
    assert "0x6" in r.stdout


def test_compare_missing_file(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["compare", "a.bin", "b.bin"])
    assert r.exit_code == 2


# ---------- grep ----------

def test_grep_text(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    Path("s.raw").write_text("hello 0x0A world\n")
    r = runner.invoke(app, ["grep", "0x0A"])
    assert r.exit_code == 0
    assert "s.raw" in r.stdout
    assert "match" in r.stdout


def test_grep_hex_pattern(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    Path("fw.bin").write_bytes(b"\x00\x0a\x1b\x00")
    r = runner.invoke(app, ["grep", "--hex", "0A1B"])
    assert r.exit_code == 0
    assert "fw.bin" in r.stdout


def test_grep_invalid_hex(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["grep", "--hex", "0A1"])
    assert r.exit_code == 2


def test_grep_no_match(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    Path("s.raw").write_text("nothing\n")
    r = runner.invoke(app, ["grep", "absent"])
    assert r.exit_code == 0
    assert "No match" in r.stdout


# ---------- analyze ----------

def test_analyze_elf(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    Path("a.elf").write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 20)
    r = runner.invoke(app, ["analyze", "a.elf"])
    assert r.exit_code == 0
    assert "ELF executable" in r.stdout
    assert "entropy" in r.stdout


def test_analyze_missing_file(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["analyze", "nope.bin"])
    assert r.exit_code == 2


# ---------- activity ----------

def test_activity_empty(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["activity"])
    assert r.exit_code == 0
    assert "No activity" in r.stdout


def test_activity_shows_events(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    log_event(proj, "build", "'x.raw' → build/x.bin (built)", level="ok")
    log_event(proj, "commit", "'x.raw' → snapshot #1")
    r = runner.invoke(app, ["activity"])
    assert r.exit_code == 0
    assert "build" in r.stdout
    assert "commit" in r.stdout
    assert "2 event(s)" in r.stdout


def test_activity_limit(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    for i in range(5):
        log_event(proj, "build", f"event {i}")
    r = runner.invoke(app, ["activity", "--limit", "2"])
    assert r.exit_code == 0
    # newest first: event 4 and 3
    assert "event 4" in r.stdout
    assert "event 0" not in r.stdout


def test_compare_capped_branch(monkeypatch, tmp_path):
    import payload.core.file_ops as fo
    monkeypatch.setattr(fo, "READ_CAP", 4)
    _init_project(tmp_path, monkeypatch)
    Path("a.bin").write_bytes(b"0123456789")
    Path("b.bin").write_bytes(b"0123456789")
    r = runner.invoke(app, ["compare", "a.bin", "b.bin"])
    assert r.exit_code == 0
    assert "Identical" in r.stdout
    assert "capped" in r.stdout


def test_cli_rename_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["rename-table", "example_table", "renamed"])
    assert r.exit_code == 0, r.stdout
    assert "renamed" in r.stdout
    assert (proj / "renamed.raw").exists()
    assert not (proj / "example_table.raw").exists()


def test_cli_rename_table_collision(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["rename-table", "example_table", "example_table"])
    assert r.exit_code != 0


def test_cli_clone(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["clone", "example_table", "copy"])
    assert r.exit_code == 0, r.stdout
    assert (proj / "copy.raw").exists()
    assert (proj / "example_table.raw").exists()
