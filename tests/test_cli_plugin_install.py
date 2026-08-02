"""CLI tests for 'pld plugin install' — see core/plugin_install.py for
the function-level tests, this file only exercises the Typer command
wiring (argument parsing, exit codes, console output)."""
from pathlib import Path

from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()

_SOURCE = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "raw_text.py"


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


def test_plugin_install_from_local_path_succeeds(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["plugin", "install", str(_SOURCE)])

    assert result.exit_code == 0, result.stdout
    assert (Path.cwd() / "plugins" / "raw_text.py").exists()
    assert "installed raw_text.py" in result.stdout
    assert "reader" in result.stdout


def test_plugin_install_twice_without_overwrite_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["plugin", "install", str(_SOURCE)])

    result = runner.invoke(app, ["plugin", "install", str(_SOURCE)])

    # PayloadError messages print via err_console (stderr), not
    # captured in result.stdout — same convention as every other CLI
    # test asserting a PayloadError failure (see test_cli_table_admin.py)
    assert result.exit_code == 2


def test_plugin_install_overwrite_flag_succeeds(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["plugin", "install", str(_SOURCE)])

    result = runner.invoke(app, ["plugin", "install", str(_SOURCE), "--overwrite"])

    assert result.exit_code == 0, result.stdout


def test_plugin_install_as_name_renames_destination(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["plugin", "install", str(_SOURCE), "--as", "renamed.py"])

    assert result.exit_code == 0, result.stdout
    assert (Path.cwd() / "plugins" / "renamed.py").exists()


def test_plugin_install_sanity_check_failure_still_installs(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    not_a_plugin = tmp_path / "not_a_plugin.py"
    not_a_plugin.write_text("x = 1\n")

    result = runner.invoke(app, ["plugin", "install", str(not_a_plugin)])

    assert result.exit_code == 0, result.stdout
    assert "sanity check failed" in result.stdout
    assert (Path.cwd() / "plugins" / "not_a_plugin.py").exists()


def test_plugin_install_then_discovered_by_plugins_list(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["plugin", "install", str(_SOURCE)])

    result = runner.invoke(app, ["plugins"])

    assert result.exit_code == 0
    assert "raw_text" in result.stdout
