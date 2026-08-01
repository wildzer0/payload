"""
CLI tests for status/commit/log/diff/restore/config show/report/export —
all via CliRunner, same pattern as test_cli_smoke.py."""
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
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No changes to save" in result.stdout


def test_status_shows_modified_after_commit(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "changed" in result.stdout


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
    assert "No table found" in result.stdout


# --- commit --------------------------------------------------------------

def test_commit_only_filters_to_named_tables(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "other.raw").write_text("0x01\n")
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["build", "other.raw", "--to", "bin"])

    result = runner.invoke(app, ["commit", "-m", "other only", "--only", "other"])

    assert result.exit_code == 0
    assert "other" in result.stdout
    assert "example_table" not in result.stdout


def test_commit_nothing_to_commit_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["commit", "-m", "second, but nothing changed"])

    assert result.exit_code == 5


def test_commit_without_building_first_is_blocked(tmp_path, monkeypatch):
    """Regression: committing a table that was never built (zero
    output, not a partial fan-out) must not produce a snapshot with no
    output attached — the user almost certainly forgot 'pld build'
    before 'pld commit'."""
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["commit", "-m", "forgot to build"])

    assert result.exit_code == 5
    assert "no output" in (result.stdout + result.stderr).lower()
    assert "pld build" in (result.stdout + result.stderr)


def test_commit_skips_table_without_output_but_commits_the_rest(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "other.raw").write_text("0x01\n")  # never built: dirty, zero output
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    result = runner.invoke(app, ["commit", "-m", "only the built one"])

    assert result.exit_code == 0, result.stdout
    assert "example_table" in result.stdout
    assert "other: skipped" in result.stdout

    log = runner.invoke(app, ["log"])
    assert "example_table" in log.stdout
    assert "other" not in log.stdout


# --- log -------------------------------------------------------------------

def test_log_no_tracked_tables_at_all(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "No table tracked" in result.stdout


def test_log_all_tables_when_no_name_given(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "example_table" in result.stdout
    assert "first" in result.stdout


def test_log_shows_current_marker_and_pipeline_info(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "current" in result.stdout
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
    runner.invoke(app, ["commit", "-m", "with explicit pipeline"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "reader:raw_text -> writer:bin" in result.stdout


def test_commit_of_partial_fanout_warns_about_missing_output(tmp_path, monkeypatch):
    """Regression: a fan-out with 2 writers where 1 fails (here 'obj'
    without toolchain.objcopy_target/arch configured, which fails
    reliably without needing a real objcopy — see
    test_obj_writer_mocked.py) must still write the successful
    writer's output, AND the subsequent commit must explicitly flag
    what's missing instead of looking like a complete snapshot like
    any other."""
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

    commit_result = runner.invoke(app, ["commit", "-m", "partial"])
    assert commit_result.exit_code == 0
    assert "incomplete pipeline" in commit_result.stdout
    assert "example_table.o" in commit_result.stdout

    log_result = runner.invoke(app, ["log"])
    assert "incomplete pipeline" in log_result.stdout


def test_restore_does_not_add_a_new_snapshot(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("0x99\n")
    runner.invoke(app, ["commit", "-m", "second"])

    runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    result = runner.invoke(app, ["log"])
    assert "#1" in result.stdout
    assert "#2" in result.stdout
    assert "#3" not in result.stdout


def test_log_skips_tracked_table_with_empty_manifest(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    tables_dir = proj / ".payload_history" / "tables"
    (tables_dir / "empty.json").write_text("[]")

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "example_table" in result.stdout
    assert "empty" not in result.stdout


# --- diff --------------------------------------------------------------

def test_diff_no_snapshot_exits_5(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["diff", "example_table"])
    assert result.exit_code == 5


def test_diff_unknown_source_exits_4(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").unlink()

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 4


def test_diff_no_difference(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 0
    assert "No difference" in result.stdout


def test_diff_shows_byte_differences(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["diff", "example_table"])

    assert result.exit_code == 0
    assert "current" in result.stdout and "snapshot" in result.stdout


def test_diff_explicit_snapshot_id(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

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
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["restore", "example_table", "1"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert (proj / "example_table.raw").read_text() == "0x99\n"


def test_restore_with_yes_flag_restores_files(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    original = (proj / "example_table.raw").read_text()
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 0
    assert (proj / "example_table.raw").read_text() == original


def test_restore_reports_removed_orphaned_output(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1 with bin"])
    runner.invoke(app, ["build", "example_table.raw", "--to", "header", "--force"])
    runner.invoke(app, ["commit", "-m", "v2 with header"])

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert not (proj / "build" / "example_table.h").exists()


def test_restore_recreates_source_deleted_from_disk(tmp_path, monkeypatch):
    """pld restore on a single-file table deleted from disk (without
    going through 'pld rm', e.g. an rm by hand) recreates it from
    scratch instead of refusing — see
    HistoryStore.source_paths_for_snapshot."""
    proj = _init_project(tmp_path, monkeypatch)
    original = (proj / "example_table.raw").read_bytes()
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").unlink()

    result = runner.invoke(app, ["restore", "example_table", "1", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "example_table.raw").read_bytes() == original


def test_restore_recreates_fully_deleted_batch_table(tmp_path, monkeypatch):
    """A batch table deleted entirely (files + [[batch_table]], e.g.
    via 'pld rm' without --member) is restorable: the source files
    come back AND the [[batch_table]] entry is re-added to
    table-tool.toml, so the table is usable again right away — see
    src/payload/docs/BATCH.md."""
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "ROW1.txt").write_text("0x01\n")
    (proj / "ROW2.txt").write_text("0x02\n")
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text() + '\n[[batch_table]]\nname = "rows"\nsources = ["ROW1.txt", "ROW2.txt"]\n'
    )
    runner.invoke(app, ["build", "rows", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first", "--only", "rows"])
    rm_result = runner.invoke(app, ["rm", "rows", "--force", "--yes"])
    assert rm_result.exit_code == 0, rm_result.stdout
    assert "[[batch_table]]" not in (proj / "table-tool.toml").read_text()

    result = runner.invoke(app, ["restore", "rows", "1", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "ROW1.txt").read_text() == "0x01\n"
    assert (proj / "ROW2.txt").read_text() == "0x02\n"
    assert 'name = "rows"' in (proj / "table-tool.toml").read_text()

    rebuild = runner.invoke(app, ["build", "rows", "--to", "bin"])
    assert rebuild.exit_code == 0, rebuild.stdout


def test_restore_recreated_batch_table_warns_about_explicit_pipeline(tmp_path, monkeypatch):
    """The reader/writer are recovered automatically, but an explicit
    multi-stage [[batch_table]].stages isn't — 'pld restore' must say
    so instead of silently dropping it."""
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "ROW1.txt").write_text("0x01\n")
    (proj / "ROW2.txt").write_text("0x02\n")
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text()
        + '\n[[batch_table]]\nname = "rows"\nsources = ["ROW1.txt", "ROW2.txt"]\n'
        + 'stages = [{ type = "reader", name = "raw_text" }, { type = "writer", name = "bin" }]\n'
    )
    runner.invoke(app, ["build", "rows"])
    runner.invoke(app, ["commit", "-m", "first", "--only", "rows"])
    rm_result = runner.invoke(app, ["rm", "rows", "--force", "--yes"])
    assert rm_result.exit_code == 0, rm_result.stdout

    result = runner.invoke(app, ["restore", "rows", "1", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert "explicit pipeline" in result.stdout
    assert "stages" in result.stdout


def test_restore_unknown_snapshot_id_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["restore", "does_not_exist", "1", "--yes"])

    assert result.exit_code == 5


def test_restore_omitted_snapshot_id_no_history_fails(tmp_path, monkeypatch):
    """Without an explicit ID, 'pld restore' uses the latest snapshot
    — if the table never had even one, it fails with a clear message
    instead of a generic 'snapshot not found' error."""
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["restore", "does_not_exist", "--yes"])

    assert result.exit_code == 5
    assert "has no snapshot" in (result.stdout + result.stderr)


def test_restore_omitted_snapshot_id_uses_last(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    original = (proj / "example_table.raw").read_bytes()
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("modified")
    runner.invoke(app, ["commit", "-m", "second"])
    (proj / "example_table.raw").write_text("still different")

    result = runner.invoke(app, ["restore", "example_table", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "example_table.raw").read_bytes() == b"modified"


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
    result = runner.invoke(app, ["config", "show", "does_not_exist"])
    assert result.exit_code == 4


# --- report ----------------------------------------------------------------

def test_report_table_never_built(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert "never built" in result.stdout
    assert "never saved" in result.stdout


def test_report_table_built_with_golden_and_snapshot(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first", "--golden"])

    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0
    assert "match" in result.stdout
    assert "#1" in result.stdout


# --- export ------------------------------------------------------------

def test_export_creates_zip_with_sources(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    out_zip = proj / "archive.zip"

    result = runner.invoke(app, ["export", str(out_zip)])

    assert result.exit_code == 0
    assert out_zip.exists()
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert any("example_table.raw" in n for n in names)


def test_export_includes_history_when_requested(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    out_zip = proj / "archive.zip"

    result = runner.invoke(app, ["export", str(out_zip), "--include-history"])

    assert result.exit_code == 0
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert any(".payload_history" in n for n in names)
