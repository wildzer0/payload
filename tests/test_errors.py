import logging
from pathlib import Path

from payload.core.errors import (
    AmbiguousReaderError,
    BatchBuildError,
    GoldenMismatchError,
    GoldenMissingError,
    InvalidCliOptionError,
    NoReaderFoundError,
    NothingToCommitError,
    PayloadError,
    PluginApiVersionError,
    ReaderParseError,
    TableNotTrackedError,
)


def test_exit_codes_match_convention():
    assert ReaderParseError(Path("x"), "err").exit_code == 1
    assert NoReaderFoundError(Path("x")).exit_code == 4
    assert GoldenMismatchError(Path("x")).exit_code == 3


def test_golden_mismatch_logs_as_warning_not_error():
    assert GoldenMismatchError(Path("x")).log_level == logging.WARNING


def test_to_dict_includes_context():
    err = ReaderParseError(Path("foo.c"), "sintassi invalida")
    d = err.to_dict()
    assert d["error"] == "ReaderParseError"
    assert d["path"] == "foo.c"


def test_batch_build_error_aggregates_failures():
    failures = [ReaderParseError(Path("a"), "e1"), ReaderParseError(Path("b"), "e2")]
    batch = BatchBuildError(failures)
    assert "2 tabelle" in batch.message
    assert len(batch.context["failures"]) == 2


def test_plugin_api_version_error_message():
    err = PluginApiVersionError("miofmt", "2.0", "1.0")
    assert "miofmt" in err.message
    assert "2.0" in err.message and "1.0" in err.message


def test_ambiguous_reader_error_lists_candidates():
    err = AmbiguousReaderError(Path("t.dat"), ["reader_a", "reader_b"])
    assert "reader_a" in err.message and "reader_b" in err.message
    assert err.context["candidates"] == ["reader_a", "reader_b"]


def test_invalid_cli_option_error_message():
    err = InvalidCliOptionError("chiave_senza_valore")
    assert "chiave_senza_valore" in err.message
    assert err.exit_code == 2


def test_golden_missing_error_is_warning_level():
    err = GoldenMissingError(Path("out.bin"))
    assert err.log_level == logging.WARNING
    assert err.exit_code == 3


def test_nothing_to_commit_error_message():
    err = NothingToCommitError()
    assert err.log_level == logging.INFO
    assert "modificata" in err.message


def test_table_not_tracked_error_message():
    err = TableNotTrackedError("sensor_temp")
    assert "sensor_temp" in err.message
    assert err.context["table_name"] == "sensor_temp"
