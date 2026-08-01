"""
Direct tests (not via CliRunner) of run_command() and _parse_opts() —
the CLI's single error/verbosity capture point. Testing it directly is
simpler than routing every branch through a real command, and it's
what every command uses underneath anyway."""
import pytest
import typer

from payload.cli import _parse_opts, run_command
from payload.core.errors import PayloadError, ToolchainExecutionError


def test_parse_opts_empty_when_none():
    assert _parse_opts(None) == {}


def test_parse_opts_parses_key_value_pairs():
    assert _parse_opts(["a=1", "b=2"]) == {"a": "1", "b": "2"}


def test_parse_opts_missing_equals_raises():
    from payload.core.errors import InvalidCliOptionError
    with pytest.raises(InvalidCliOptionError):
        _parse_opts(["no_equals_sign"])


def test_parse_opts_empty_key_raises():
    from payload.core.errors import InvalidCliOptionError
    with pytest.raises(InvalidCliOptionError):
        _parse_opts(["=value"])


def test_run_command_prints_payload_error_and_exits_with_its_code(capsys):
    def _boom():
        raise ToolchainExecutionError(["cmd"], 1, "compilation error")

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_boom, verbosity=0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert "compilation error" not in captured.err  # no -vv, no stderr dump
    assert "Command" in captured.err


def test_run_command_verbose_dumps_stderr_and_stdout_from_context():
    class _CustomError(PayloadError):
        exit_code = 1

    def _boom():
        raise _CustomError("failed", stderr="stderr details", stdout="stdout details")

    import io
    from contextlib import redirect_stderr

    buf = io.StringIO()
    with redirect_stderr(buf):
        with pytest.raises(typer.Exit):
            run_command(_boom, verbosity=2)

    output = buf.getvalue()
    assert "stderr details" in output
    assert "stdout details" in output


def test_run_command_reraises_typer_exit_unchanged():
    def _exits():
        raise typer.Exit(code=7)

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_exits, verbosity=0)
    assert exc_info.value.exit_code == 7


def test_run_command_wraps_unexpected_exception(capsys):
    def _bug():
        raise RuntimeError("internal bug never anticipated")

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_bug, verbosity=0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert "Unexpected internal error" in captured.err
