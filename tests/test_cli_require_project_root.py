"""Like 'git' outside a repository: commands that operate on a
specific project must refuse to run until a table-tool.toml has been
created with 'pld init' — see require_project_root in cli.py. 'init'
and the plugin-agnostic commands (view, plugin
validate/new/new-local) stay usable anywhere, though."""
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
    assert "is not an initialized payload project" in (result.stdout + result.stderr)


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
