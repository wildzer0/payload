"""
Test CLI per le tabelle batch ([[batch_table]], vedi
src/payload/docs/BATCH.md) — stesso pattern (CliRunner) di
test_cli_history_commands.py, ma usa il reader raw_text REALE (che
implementa parse_many) invece di plugin fake, dato che 'pld init'
produce già un progetto configurato con quel reader e writer 'bin'.
"""
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


def _add_batch_table(proj, name="rows", sources='["ROW*.txt"]', extra=""):
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text()
        + f'\n[[batch_table]]\nname = "{name}"\nsources = {sources}\n{extra}'
    )


def _write_rows(proj, n=2):
    for i in range(1, n + 1):
        (proj / f"ROW{i}.txt").write_text(f"0x0{i}\n")


def test_build_batch_table_by_name(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["build", "rows"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "build" / "rows.bin").exists()
    assert (proj / "build" / "rows.bin").read_bytes() == bytes([0x01, 0x02])


def test_build_unknown_name_is_neither_file_nor_batch_table(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["build", "does_not_exist"])

    assert result.exit_code == 4
    assert "non trovato" in (result.stdout + result.stderr)


def test_build_all_includes_batch_tables_and_excludes_member_files(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["build-all"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "build" / "rows.bin").exists()
    # ROW1.txt/ROW2.txt non devono essere buildati ANCHE come tabelle
    # a sé stanti (sarebbero "ROW1"/"ROW2", duplicati/confusi)
    assert not (proj / "build" / "ROW1.bin").exists()
    assert not (proj / "build" / "ROW2.bin").exists()


def test_status_shows_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "rows" in result.stdout
    assert "batch" in result.stdout


def test_commit_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])

    result = runner.invoke(app, ["commit", "-m", "primo batch"])

    assert result.exit_code == 0, result.stdout
    assert "rows" in result.stdout

    log_result = runner.invoke(app, ["log", "rows"])
    assert log_result.exit_code == 0
    assert "primo batch" in log_result.stdout


def test_commit_then_status_clean_for_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    result = runner.invoke(app, ["status"])

    assert "modificata" not in result.stdout
    assert "mai salvata" not in result.stdout


def test_status_dirty_after_editing_one_member_file(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    (proj / "ROW2.txt").write_text("0x99\n")

    result = runner.invoke(app, ["status"])
    assert "modificata" in result.stdout


def test_diff_batch_table_shows_changed_member_file(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    (proj / "ROW2.txt").write_text("0x99\n")

    result = runner.invoke(app, ["diff", "rows"])

    assert result.exit_code == 0
    assert "ROW2.txt" in result.stdout


def test_diff_batch_table_no_difference(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    result = runner.invoke(app, ["diff", "rows"])

    assert result.exit_code == 0
    assert "Nessuna differenza" in result.stdout


def test_restore_batch_table_writes_back_all_members(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    (proj / "ROW1.txt").write_text("0xFF\n")
    (proj / "ROW2.txt").write_text("0xEE\n")

    result = runner.invoke(app, ["restore", "rows", "1", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "ROW1.txt").read_text() == "0x01\n"
    assert (proj / "ROW2.txt").read_text() == "0x02\n"


def test_golden_set_and_check_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])
    runner.invoke(app, ["golden", "set", "rows"])

    result = runner.invoke(app, ["golden", "check", "rows"])

    assert result.exit_code == 0
    assert "match" in result.stdout


def test_golden_check_all_includes_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])
    runner.invoke(app, ["golden", "set", "rows"])

    result = runner.invoke(app, ["golden", "check"])

    assert result.exit_code == 0
    assert "rows" in result.stdout


def test_golden_diff_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])
    runner.invoke(app, ["golden", "set", "rows"])

    result = runner.invoke(app, ["golden", "diff", "rows"])

    assert result.exit_code == 0
    assert "Nessuna differenza" in result.stdout


def test_report_shows_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1"])

    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0
    assert "rows" in result.stdout
    assert "batch" in result.stdout


def test_export_includes_batch_member_files(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    output_zip = tmp_path / "export.zip"
    result = runner.invoke(app, ["export", str(output_zip)])

    assert result.exit_code == 0, result.stdout
    import zipfile
    with zipfile.ZipFile(output_zip) as zf:
        names = zf.namelist()
    assert "ROW1.txt" in names
    assert "ROW2.txt" in names
    assert "table-tool.toml" in names


def test_config_show_batch_table_falls_back_to_global(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["config", "show", "rows"])

    assert result.exit_code == 0
    assert "defaults.writer" in result.stdout


def test_pipeline_show_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["pipeline", "show", "rows"])

    assert result.exit_code == 0, result.stdout
    assert "raw_text" in result.stdout
    assert "bin" in result.stdout
    assert "rows.bin" in result.stdout


def test_pipeline_show_batch_table_uses_inline_reader_writer_override(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj, extra='writer = "hex"\n')

    result = runner.invoke(app, ["pipeline", "show", "rows"])

    assert result.exit_code == 0, result.stdout
    assert "hex" in result.stdout


def test_watch_initial_sweep_includes_batch_table(tmp_path, monkeypatch):
    """La build iniziale di 'pld watch' include le tabelle batch (il
    live-reload no, vedi BATCH.md) — verificato tramite --filter
    puntato a una cartella inesistente per far terminare subito il
    watch loop dopo la build iniziale non è praticabile (watch blocca);
    verifichiamo invece che l'esclusione dei membri dalla discovery
    'grezza' iniziale non faccia crashare nulla chiamando build-all
    (stesso codepath di preparazione)."""
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["build-all", "--dry-run"])

    assert result.exit_code == 0, result.stdout
