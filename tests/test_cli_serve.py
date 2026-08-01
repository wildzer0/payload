"""Test for the 'pld serve' command — uvicorn.run() is always mocked:
it's blocking by design (same reason 'pld watch' uses a mocked
watch_loop in its tests, see tests/test_watch.py)."""
import sys
from unittest.mock import patch

from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


def test_serve_missing_deps_shows_clear_error(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "uvicorn", None)  # forces ImportError on 'import uvicorn'

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 2
    assert "payload[serve]" in result.stdout


def test_serve_starts_uvicorn_with_configured_host_port(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)

    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(app, ["serve", "--port", "9999"])

    assert result.exit_code == 0
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9999
    assert proj.name in result.stdout


def test_serve_default_host_no_warning(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    with patch("uvicorn.run"):
        result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert "WARNING" not in (result.stdout + result.stderr)


def test_serve_non_localhost_host_warns(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    with patch("uvicorn.run"):
        result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert "exposed" in result.stderr
