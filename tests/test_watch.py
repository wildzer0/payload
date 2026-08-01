"""
CLI test for 'pld watch' — checks that the initial build happens
BEFORE the live watch starts. watch_loop blocks forever, so it must
always be monkeypatched to a no-op ('payload.cli.watch_loop', the name
it's imported under in cli.py) to make the invocation return.
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
    # duplicates example_table's stem -> the initial build fails with
    # DuplicateTableNameError, but watch_loop must still start
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
