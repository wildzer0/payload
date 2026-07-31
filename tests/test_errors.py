import logging
from pathlib import Path

from payload.core.errors import (
    BatchBuildError,
    GoldenMismatchError,
    NoReaderFoundError,
    PayloadError,
    ReaderParseError,
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
