"""Come 'git' fuori da un repository: i comandi che operano su un
progetto specifico devono rifiutarsi di eseguire finché non è stato
creato un table-tool.toml con 'pld init' — vedi require_project_root
in cli.py. 'init' e i comandi plugin-agnostici (view, plugin
validate/new/new-local) restano invece utilizzabili ovunque."""
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def test_doctor_outside_project_fails_with_clear_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code != 0
    assert "pld init" in (result.stdout + result.stderr)


def test_status_outside_project_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "non è un progetto payload inizializzato" in (result.stdout + result.stderr)


def test_build_outside_project_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.raw").write_text("hello")

    result = runner.invoke(app, ["build", "t.raw"])

    assert result.exit_code != 0
    assert "pld init" in (result.stdout + result.stderr)


def test_build_all_outside_project_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["build-all"])

    assert result.exit_code != 0
    assert "pld init" in (result.stdout + result.stderr)


def test_serve_outside_project_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code != 0
    assert "pld init" in (result.stdout + result.stderr)


def test_clean_outside_project_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["clean"])

    assert result.exit_code != 0
    assert "pld init" in (result.stdout + result.stderr)


def test_commands_work_again_once_initialized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    monkeypatch.chdir(tmp_path / "proj")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0


def test_view_does_not_require_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "t.raw"
    src.write_text("0x0A, 0x1B  # offset 0\n")

    result = runner.invoke(app, ["view", "t.raw"])

    assert result.exit_code == 0


def test_plugin_new_does_not_require_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["plugin", "new", "my-reader", "--kind", "reader"])

    assert result.exit_code == 0
