"""CLI tests for 'pld rm' and 'pld import' — same pattern
(CliRunner, real raw_text reader) as test_cli_batch_tables.py."""
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


def _add_batch_table(proj, name="rows", sources='["ROW1.txt", "ROW2.txt"]'):
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text() + f'\n[[batch_table]]\nname = "{name}"\nsources = {sources}\n'
    )


def _write_rows(proj, n=2):
    for i in range(1, n + 1):
        (proj / f"ROW{i}.txt").write_text(f"0x0{i}\n")


# --- pld rm ------------------------------------------------------------------

def test_rm_without_force_refuses(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["rm", "example_table"])

    assert result.exit_code == 2
    assert "--force" in result.stdout
    assert (tmp_path / "proj" / "example_table.raw").exists()


def test_rm_interactive_confirm_yes(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["rm", "example_table", "--force"], input="y\n")

    assert result.exit_code == 0, result.stdout
    assert not (proj / "example_table.raw").exists()


def test_rm_interactive_confirm_no_cancels(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["rm", "example_table", "--force"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert (proj / "example_table.raw").exists()


def test_rm_with_yes_skips_prompt_and_deletes(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])

    result = runner.invoke(app, ["rm", "example_table", "--force", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert not (proj / "example_table.raw").exists()
    assert not (proj / "build" / "example_table.bin").exists()
    # the history is not touched
    log = runner.invoke(app, ["log", "example_table"])
    assert "#1" in log.stdout


def test_rm_warns_when_dirty(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "first"])
    (proj / "example_table.raw").write_text("0x0A, 0x1B  # modified\n")

    result = runner.invoke(app, ["rm", "example_table", "--force"], input="n\n")

    assert "uncommitted changes" in result.stdout


def test_rm_unknown_table_exits_error(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["rm", "does_not_exist", "--force", "--yes"])

    assert result.exit_code != 0


def test_rm_member_on_non_batch_table_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["rm", "example_table", "--member", "x", "--force", "--yes"])

    assert result.exit_code == 2
    assert "member" in result.stdout.lower()


def test_rm_member_unknown_filename_fails(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--member", "does_not_exist.txt", "--force", "--yes"])

    assert result.exit_code == 4


def test_rm_member_interactive_confirm_yes(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--member", "ROW1.txt", "--force"], input="y\n")

    assert result.exit_code == 0, result.stdout
    assert not (proj / "ROW1.txt").exists()


def test_rm_member_interactive_confirm_no_cancels(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--member", "ROW1.txt", "--force"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert (proj / "ROW1.txt").exists()


def test_rm_whole_batch_table_preview_mentions_config_removal(tmp_path, monkeypatch):
    """Without --yes, the preview of an rm on an entire batch table
    must explicitly warn that the [[batch_table]] will also be removed
    from table-tool.toml, not just the files."""
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--force"], input="n\n")

    assert "batch_table" in result.stdout
    assert (proj / "ROW1.txt").exists()


def test_rm_batch_member_keeps_entry(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--member", "ROW1.txt", "--force", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert not (proj / "ROW1.txt").exists()
    assert (proj / "ROW2.txt").exists()
    assert 'name = "rows"' in (proj / "table-tool.toml").read_text()


def test_rm_whole_batch_table_removes_config_entry(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj)

    result = runner.invoke(app, ["rm", "rows", "--force", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert "batch_table" in result.stdout
    assert not (proj / "ROW1.txt").exists()
    assert not (proj / "ROW2.txt").exists()
    assert 'name = "rows"' not in (proj / "table-tool.toml").read_text()


# --- pld import ----------------------------------------------------------

def test_import_single_file(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01, 0x02\n")

    result = runner.invoke(app, ["import", str(external)])

    assert result.exit_code == 0, result.stdout
    assert (proj / "external.raw").read_text() == "0x01, 0x02\n"


def test_import_single_file_custom_name(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01\n")

    result = runner.invoke(app, ["import", str(external), "--as", "custom"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "custom.raw").exists()
    assert not (proj / "external.raw").exists()


def test_import_empty_file_fails(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_bytes(b"")

    result = runner.invoke(app, ["import", str(external)])

    assert result.exit_code == 2
    assert not (proj / "external.raw").exists()


def test_import_missing_external_file_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(tmp_path / "does_not_exist.raw")])

    assert result.exit_code == 4


def test_import_collision_without_overwrite_fails(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01\n")
    runner.invoke(app, ["import", str(external)])

    result = runner.invoke(app, ["import", str(external)])

    assert result.exit_code != 0
    assert (proj / "external.raw").read_text() == "0x01\n"


def test_import_overwrite_updates_existing(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01\n")
    runner.invoke(app, ["import", str(external)])
    external.write_text("0x02\n")

    result = runner.invoke(app, ["import", str(external), "--overwrite"])

    assert result.exit_code == 0, result.stdout
    assert "updated" in result.stdout
    assert (proj / "external.raw").read_text() == "0x02\n"


def test_import_new_batch_from_multiple_files(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    row1 = tmp_path / "ROW1.txt"
    row1.write_text("0x01\n")
    row2 = tmp_path / "ROW2.txt"
    row2.write_text("0x02\n")

    result = runner.invoke(app, ["import", str(row1), str(row2), "--new-batch", "rows"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "ROW1.txt").exists() and (proj / "ROW2.txt").exists()
    assert 'name = "rows"' in (proj / "table-tool.toml").read_text()


def test_import_new_batch_missing_file_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(tmp_path / "does_not_exist.txt"), "--new-batch", "rows"])

    assert result.exit_code == 4


def test_import_batch_and_new_batch_incompatible(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01\n")

    result = runner.invoke(app, ["import", str(external), "--batch", "a", "--new-batch", "b"])

    assert result.exit_code == 2
    assert "incompatible" in result.stdout


def test_import_multiple_files_without_new_batch_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    row1 = tmp_path / "ROW1.txt"
    row1.write_text("0x01\n")
    row2 = tmp_path / "ROW2.txt"
    row2.write_text("0x02\n")

    result = runner.invoke(app, ["import", str(row1), str(row2)])

    assert result.exit_code == 2


def test_import_into_existing_batch_table(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    _write_rows(proj)
    _add_batch_table(proj, sources='["ROW1.txt", "ROW2.txt"]')
    row3 = tmp_path / "ROW3.txt"
    row3.write_text("0x03\n")

    result = runner.invoke(app, ["import", str(row3), "--batch", "rows"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "ROW3.txt").exists()
    assert '"ROW3.txt"' in (proj / "table-tool.toml").read_text()


def test_import_into_unknown_batch_table_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    external = tmp_path / "external.raw"
    external.write_text("0x01\n")

    result = runner.invoke(app, ["import", str(external), "--batch", "does_not_exist"])

    assert result.exit_code == 4
