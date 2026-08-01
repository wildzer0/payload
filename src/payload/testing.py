"""
Conformance suite for reader/writer plugins.

Doesn't check "do tests exist" (impossible at runtime — tests aren't
shipped with the installed package). Instead it checks that the plugin
really honors the contract described in src/payload/docs/PLUGINS.md,
both structurally (required attributes) and behaviorally (parse/emit
produce what they're supposed to produce).

Usage from pytest (in your own plugin):

    from payload.testing import assert_reader_conforms

    def test_my_reader_conforms(tmp_path):
        sample = tmp_path / "sample.myext"
        sample.write_text("...")
        assert_reader_conforms(MyReader(), sample)

Usage from the CLI: 'pld plugin validate <name> --sample <file>'.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from payload.core.errors import PayloadError
from payload.core.ir import TableIR


@dataclass
class ConformanceIssue:
    check: str
    detail: str


def check_reader_structure(reader) -> list[ConformanceIssue]:
    """Static checks, don't require a sample file."""
    issues = []
    if not getattr(reader, "name", None):
        issues.append(ConformanceIssue("name", "missing or empty 'name' attribute"))
    if not getattr(reader, "api_version", None):
        issues.append(ConformanceIssue("api_version", "missing 'api_version' attribute"))
    extensions = getattr(reader, "extensions", None)
    if not extensions or not isinstance(extensions, list):
        issues.append(ConformanceIssue("extensions", "must be a non-empty list"))
    elif not all(isinstance(e, str) and e.startswith(".") for e in extensions):
        issues.append(ConformanceIssue("extensions", "each extension must be a string starting with '.'"))
    if not hasattr(reader, "parse") or not callable(reader.parse):
        issues.append(ConformanceIssue("parse", "missing or non-callable 'parse' method"))
    if not hasattr(reader, "sniff") or not callable(reader.sniff):
        issues.append(ConformanceIssue("sniff", "missing or non-callable 'sniff' method"))
    return issues


def check_reader_behavior(reader, sample_path: Path) -> list[ConformanceIssue]:
    """Behavioral checks: require a valid sample file."""
    issues = []
    try:
        ir = reader.parse(sample_path, {})
    except PayloadError:
        issues.append(ConformanceIssue(
            "parse", f"parse() raised an error on the valid sample {sample_path}"
        ))
        return issues
    except Exception as e:
        issues.append(ConformanceIssue(
            "parse",
            f"parse() raised an unhandled {type(e).__name__} instead of ReaderParseError: {e}",
        ))
        return issues

    if not isinstance(ir, TableIR):
        issues.append(ConformanceIssue("parse", f"parse() must return TableIR, not {type(ir).__name__}"))
        return issues
    if not isinstance(ir.data, bytes):
        issues.append(ConformanceIssue("data", f"TableIR.data must be bytes, not {type(ir.data).__name__}"))
    if not ir.name:
        issues.append(ConformanceIssue("name", "TableIR.name is empty"))
    if ir.source_format != reader.name:
        issues.append(ConformanceIssue(
            "source_format", f"expected '{reader.name}', found '{ir.source_format}'"
        ))
    if ir.source_path != sample_path:
        issues.append(ConformanceIssue("source_path", "doesn't match the file actually read"))
    return issues


def check_writer_structure(writer) -> list[ConformanceIssue]:
    issues = []
    if not getattr(writer, "name", None):
        issues.append(ConformanceIssue("name", "missing or empty 'name' attribute"))
    if not getattr(writer, "api_version", None):
        issues.append(ConformanceIssue("api_version", "missing 'api_version' attribute"))
    extension = getattr(writer, "extension", None)
    if not extension or not isinstance(extension, str) or not extension.startswith("."):
        issues.append(ConformanceIssue("extension", "must be a string starting with '.'"))
    if not hasattr(writer, "emit") or not callable(writer.emit):
        issues.append(ConformanceIssue("emit", "missing or non-callable 'emit' method"))
    return issues


def check_writer_behavior(writer, sample_ir: TableIR, tmp_dir: Path) -> list[ConformanceIssue]:
    issues = []
    out_path = tmp_dir / f"{sample_ir.name}{writer.extension}"
    try:
        result = writer.emit(sample_ir, out_path, {})
    except PayloadError:
        issues.append(ConformanceIssue("emit", "emit() raised an error on a valid IR"))
        return issues
    except Exception as e:
        issues.append(ConformanceIssue(
            "emit", f"emit() raised an unhandled {type(e).__name__} instead of WriterEmitError: {e}"
        ))
        return issues

    if not isinstance(result, Path):
        issues.append(ConformanceIssue("emit", f"emit() must return a Path, not {type(result).__name__}"))
        return issues
    if not result.exists():
        issues.append(ConformanceIssue("emit", f"the declared file {result} doesn't exist on disk"))
    return issues


def assert_reader_conforms(reader, sample_path: Path) -> None:
    """For use in pytest: raises AssertionError with detail if non-conforming."""
    issues = check_reader_structure(reader) + check_reader_behavior(reader, sample_path)
    if issues:
        details = "\n".join(f"  - [{i.check}] {i.detail}" for i in issues)
        raise AssertionError(f"{reader.name} doesn't conform to the Reader contract:\n{details}")


def assert_writer_conforms(writer, sample_ir: TableIR, tmp_dir: Path) -> None:
    issues = check_writer_structure(writer) + check_writer_behavior(writer, sample_ir, tmp_dir)
    if issues:
        details = "\n".join(f"  - [{i.check}] {i.detail}" for i in issues)
        raise AssertionError(f"{writer.name} doesn't conform to the Writer contract:\n{details}")
