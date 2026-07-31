"""
Test diretti (non tramite CliRunner) di run_command() e _parse_opts() —
il punto unico di cattura errori/verbosità del CLI. Verificarlo
direttamente è più semplice che instradare ogni ramo attraverso un
comando reale, ed è comunque quello che ogni comando usa sotto."""
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
        _parse_opts(["senza_uguale"])


def test_parse_opts_empty_key_raises():
    from payload.core.errors import InvalidCliOptionError
    with pytest.raises(InvalidCliOptionError):
        _parse_opts(["=valore"])


def test_run_command_prints_payload_error_and_exits_with_its_code(capsys):
    def _boom():
        raise ToolchainExecutionError(["cmd"], 1, "errore di compilazione")

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_boom, verbosity=0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert "errore di compilazione" not in captured.err  # senza -vv, niente dump stderr
    assert "Comando" in captured.err


def test_run_command_verbose_dumps_stderr_and_stdout_from_context():
    class _CustomError(PayloadError):
        exit_code = 1

    def _boom():
        raise _CustomError("fallito", stderr="dettagli stderr", stdout="dettagli stdout")

    import io
    from contextlib import redirect_stderr

    buf = io.StringIO()
    with redirect_stderr(buf):
        with pytest.raises(typer.Exit):
            run_command(_boom, verbosity=2)

    output = buf.getvalue()
    assert "dettagli stderr" in output
    assert "dettagli stdout" in output


def test_run_command_reraises_typer_exit_unchanged():
    def _exits():
        raise typer.Exit(code=7)

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_exits, verbosity=0)
    assert exc_info.value.exit_code == 7


def test_run_command_wraps_unexpected_exception(capsys):
    def _bug():
        raise RuntimeError("bug interno mai previsto")

    with pytest.raises(typer.Exit) as exc_info:
        run_command(_bug, verbosity=0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert "Errore interno inatteso" in captured.err
