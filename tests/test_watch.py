"""
Test CLI per 'pld watch' — verifica che la build iniziale avvenga
PRIMA che parta il watch live. watch_loop blocca per sempre, quindi va
sempre monkeypatchato a no-op ('payload.cli.watch_loop', il nome con
cui è importato in cli.py) per far ritornare l'invocazione.
"""
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _noop_watch_loop(root, known_ext, out, on_change):
    return


def test_watch_runs_initial_build_before_watching(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)
    monkeypatch.setattr("payload.cli.watch_loop", _noop_watch_loop)

    result = runner.invoke(app, ["watch", "."])

    assert result.exit_code == 0
    assert (proj / "build" / "example_table.bin").exists()


def test_watch_proceeds_when_initial_build_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    # duplica lo stem di example_table -> la build iniziale fallisce con
    # DuplicateTableNameError, ma watch_loop deve comunque partire
    (proj / "extra" / "sub").mkdir(parents=True)
    (proj / "extra" / "sub" / "example_table.raw").write_text("0x01\n")

    called = []
    monkeypatch.setattr(
        "payload.cli.watch_loop",
        lambda root, known_ext, out, on_change: called.append(True),
    )
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["watch", "."])

    assert result.exit_code == 0
    assert called == [True]


def test_watch_jobs_and_filter_options_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)
    monkeypatch.setattr("payload.cli.watch_loop", _noop_watch_loop)

    result = runner.invoke(app, ["watch", ".", "--jobs", "2", "--filter", "*.raw"])

    assert result.exit_code == 0
