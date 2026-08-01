"""
Test CLI per status/commit/log/diff/restore/config show/report/export —
tutti via CliRunner, stesso pattern di test_cli_smoke.py."""
import zipfile

from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


# --- status ------------------------------------------------------------

def test_status_no_change_after_commit(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Nessuna modifica da salvare" in result.stdout


def test_status_shows_modified_after_commit(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "modificata" in result.stdout


def test_status_duplicate_table_names_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "sub").mkdir()
    (proj / "sub" / "example_table.raw").write_text("0x01\n")

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0


def test_status_no_tables_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "table-tool.toml").write_text("")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Nessuna tabella trovata" in result.stdout


# --- commit --------------------------------------------------------------

def test_commit_only_filters_to_named_tables(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "altra.raw").write_text("0x01\n")
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["build", "altra.raw", "--to", "bin"])

    result = runner.invoke(app, ["commit", "-m", "solo altra", "--only", "altra"])

    assert result.exit_code == 0
    assert "altra" in result.stdout
    assert "example_table" not in result.stdout


def test_commit_nothing_to_commit_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["commit", "-m", "secondo, ma niente e' cambiato"])

    assert result.exit_code == 5


# --- log -------------------------------------------------------------------

def test_log_no_tracked_tables_at_all(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "Nessuna tabella tracciata" in result.stdout


def test_log_all_tables_when_no_name_given(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "example_table" in result.stdout
    assert "primo" in result.stdout


def test_log_shows_current_marker_and_pipeline_info(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "attuale" in result.stdout
    assert "bin" in result.stdout


def test_log_shows_full_pipeline_description_when_explicit(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }'
        ']\n'
    )
    runner.invoke(app, ["build", "example_table.raw"])
    runner.invoke(app, ["commit", "-m", "con pipeline esplicita"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "reader:raw_text -> writer:bin" in result.stdout


def test_commit_of_partial_fanout_warns_about_missing_output(tmp_path, monkeypatch):
    """Regressione: un fan-out con 2 writer di cui 1 fallisce (qui
    'obj' senza toolchain.objcopy_target/arch configurati, che fallisce
    in modo affidabile senza bisogno di un vero objcopy — vedi
    test_obj_writer_mocked.py) deve comunque scrivere l'output del
    writer riuscito, E il commit successivo deve segnalare
    esplicitamente cosa manca invece di sembrare uno snapshot completo
    come un altro."""
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }, '
        '{ type = "writer", name = "obj" }'
        ']\n'
    )

    build_result = runner.invoke(app, ["build", "example_table.raw"])
    build_output = build_result.stdout + build_result.stderr
    assert build_result.exit_code == 1
    assert "bin" in build_output
    assert "obj" in build_output
    assert (proj / "build" / "example_table.bin").exists()
    assert not (proj / "build" / "example_table.o").exists()

    commit_result = runner.invoke(app, ["commit", "-m", "parziale"])
    assert commit_result.exit_code == 0
    assert "pipeline incompleta" in commit_result.stdout
    assert "example_table.o" in commit_result.stdout

    log_result = runner.invoke(app, ["log"])
    assert "pipeline incompleta" in log_result.stdout


def test_restore_does_not_add_a_new_snapshot(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").write_text("0x99\n")
    runner.invoke(app, ["commit", "-m", "secondo"])

    runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    result = runner.invoke(app, ["log"])
    assert "#1" in result.stdout
    assert "#2" in result.stdout
    assert "#3" not in result.stdout


def test_log_skips_tracked_table_with_empty_manifest(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    tables_dir = proj / ".payload_history" / "tables"
    (tables_dir / "vuota.json").write_text("[]")

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "example_table" in result.stdout
    assert "vuota" not in result.stdout


# --- diff --------------------------------------------------------------

def test_diff_no_snapshot_exits_5(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["diff", "example_table"])
    assert result.exit_code == 5


def test_diff_unknown_source_exits_4(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").unlink()

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 4


def test_diff_no_difference(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 0
    assert "Nessuna differenza" in result.stdout


def test_diff_shows_byte_differences(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 0
    assert "attuale" in result.stdout and "snapshot" in result.stdout


def test_diff_explicit_snapshot_id(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])

    result = runner.invoke(app, ["diff", "example_table", "--snapshot", "1"])

    assert result.exit_code == 0


# --- restore -----------------------------------------------------------

def test_restore_unknown_snapshot_exits_nonzero(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])
    assert result.exit_code != 0


def test_restore_declined_confirmation_does_nothing(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["restore", "example_table", "1"], input="n\n")

    assert result.exit_code == 0
    assert "Annullato" in result.stdout
    assert (proj / "example_table.raw").read_text() == "0x99\n"


def test_restore_with_yes_flag_restores_files(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    original = (proj / "example_table.raw").read_text()
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 0
    assert (proj / "example_table.raw").read_text() == original


def test_restore_reports_removed_orphaned_output(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1 con bin"])
    runner.invoke(app, ["build", "example_table.raw", "--to", "header", "--force"])
    runner.invoke(app, ["commit", "-m", "v2 con header"])

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 0
    assert "rimosso" in result.stdout
    assert not (proj / "build" / "example_table.h").exists()


def test_restore_unknown_source_exits_4(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    (proj / "example_table.raw").unlink()

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 4


# --- config show ---------------------------------------------------------

def test_config_show_global(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "defaults.writer" in result.stdout


def test_config_show_for_table_with_sidecar(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "example_table.config.toml").write_text('[plugin.custom]\nkey = "value"\n')

    result = runner.invoke(app, ["config", "show", "example_table"])

    assert result.exit_code == 0
    assert "sidecar" in result.stdout
    assert "plugin.custom.key" in result.stdout


def test_config_show_unknown_table_exits_4(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["config", "show", "non_esiste"])
    assert result.exit_code == 4


# --- report ----------------------------------------------------------------

def test_report_table_never_built(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert "mai buildata" in result.stdout
    assert "mai salvata" in result.stdout


def test_report_table_built_with_golden_and_snapshot(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo", "--golden"])

    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0
    assert "match" in result.stdout
    assert "#1" in result.stdout


# --- export ------------------------------------------------------------

def test_export_creates_zip_with_sources(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    out_zip = proj / "archivio.zip"

    result = runner.invoke(app, ["export", str(out_zip)])

    assert result.exit_code == 0
    assert out_zip.exists()
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert any("example_table.raw" in n for n in names)


def test_export_includes_history_when_requested(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "primo"])
    out_zip = proj / "archivio.zip"

    result = runner.invoke(app, ["export", str(out_zip), "--include-history"])

    assert result.exit_code == 0
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert any(".payload_history" in n for n in names)
