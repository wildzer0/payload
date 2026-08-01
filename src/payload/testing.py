"""
Suite di conformità per plugin reader/writer.

Non verifica "esistono dei test" (impossibile a runtime — i test non
vengono distribuiti col pacchetto installato). Verifica invece che il
plugin rispetti davvero il contratto descritto in src/payload/docs/PLUGINS.md,
sia a livello strutturale (attributi richiesti) sia comportamentale
(parse/emit producono quello che devono produrre).

Uso da pytest (nel proprio plugin):

    from payload.testing import assert_reader_conforms

    def test_my_reader_conforms(tmp_path):
        sample = tmp_path / "sample.myext"
        sample.write_text("...")
        assert_reader_conforms(MyReader(), sample)

Uso da CLI: 'pld plugin validate <nome> --sample <file>'.
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
    """Check statici, non richiedono un file di esempio."""
    issues = []
    if not getattr(reader, "name", None):
        issues.append(ConformanceIssue("name", "attributo 'name' mancante o vuoto"))
    if not getattr(reader, "api_version", None):
        issues.append(ConformanceIssue("api_version", "attributo 'api_version' mancante"))
    extensions = getattr(reader, "extensions", None)
    if not extensions or not isinstance(extensions, list):
        issues.append(ConformanceIssue("extensions", "deve essere una lista non vuota"))
    elif not all(isinstance(e, str) and e.startswith(".") for e in extensions):
        issues.append(ConformanceIssue("extensions", "ogni estensione deve essere una stringa che inizia con '.'"))
    if not hasattr(reader, "parse") or not callable(reader.parse):
        issues.append(ConformanceIssue("parse", "metodo 'parse' mancante o non chiamabile"))
    if not hasattr(reader, "sniff") or not callable(reader.sniff):
        issues.append(ConformanceIssue("sniff", "metodo 'sniff' mancante o non chiamabile"))
    return issues


def check_reader_behavior(reader, sample_path: Path) -> list[ConformanceIssue]:
    """Check comportamentali: richiedono un file di esempio valido."""
    issues = []
    try:
        ir = reader.parse(sample_path, {})
    except PayloadError:
        issues.append(ConformanceIssue(
            "parse", f"parse() ha sollevato un errore sul sample valido {sample_path}"
        ))
        return issues
    except Exception as e:
        issues.append(ConformanceIssue(
            "parse",
            f"parse() ha sollevato {type(e).__name__} non gestita invece di ReaderParseError: {e}",
        ))
        return issues

    if not isinstance(ir, TableIR):
        issues.append(ConformanceIssue("parse", f"parse() deve ritornare TableIR, non {type(ir).__name__}"))
        return issues
    if not isinstance(ir.data, bytes):
        issues.append(ConformanceIssue("data", f"TableIR.data deve essere bytes, non {type(ir.data).__name__}"))
    if not ir.name:
        issues.append(ConformanceIssue("name", "TableIR.name è vuoto"))
    if ir.source_format != reader.name:
        issues.append(ConformanceIssue(
            "source_format", f"atteso '{reader.name}', trovato '{ir.source_format}'"
        ))
    if ir.source_path != sample_path:
        issues.append(ConformanceIssue("source_path", "non corrisponde al file effettivamente letto"))
    return issues


def check_writer_structure(writer) -> list[ConformanceIssue]:
    issues = []
    if not getattr(writer, "name", None):
        issues.append(ConformanceIssue("name", "attributo 'name' mancante o vuoto"))
    if not getattr(writer, "api_version", None):
        issues.append(ConformanceIssue("api_version", "attributo 'api_version' mancante"))
    extension = getattr(writer, "extension", None)
    if not extension or not isinstance(extension, str) or not extension.startswith("."):
        issues.append(ConformanceIssue("extension", "deve essere una stringa che inizia con '.'"))
    if not hasattr(writer, "emit") or not callable(writer.emit):
        issues.append(ConformanceIssue("emit", "metodo 'emit' mancante o non chiamabile"))
    return issues


def check_writer_behavior(writer, sample_ir: TableIR, tmp_dir: Path) -> list[ConformanceIssue]:
    issues = []
    out_path = tmp_dir / f"{sample_ir.name}{writer.extension}"
    try:
        result = writer.emit(sample_ir, out_path, {})
    except PayloadError:
        issues.append(ConformanceIssue("emit", "emit() ha sollevato un errore su una IR valida"))
        return issues
    except Exception as e:
        issues.append(ConformanceIssue(
            "emit", f"emit() ha sollevato {type(e).__name__} non gestita invece di WriterEmitError: {e}"
        ))
        return issues

    if not isinstance(result, Path):
        issues.append(ConformanceIssue("emit", f"emit() deve ritornare un Path, non {type(result).__name__}"))
        return issues
    if not result.exists():
        issues.append(ConformanceIssue("emit", f"il file dichiarato {result} non esiste su disco"))
    return issues


def assert_reader_conforms(reader, sample_path: Path) -> None:
    """Da usare in pytest: solleva AssertionError con dettaglio se non conforme."""
    issues = check_reader_structure(reader) + check_reader_behavior(reader, sample_path)
    if issues:
        details = "\n".join(f"  - [{i.check}] {i.detail}" for i in issues)
        raise AssertionError(f"{reader.name} non è conforme al contratto Reader:\n{details}")


def assert_writer_conforms(writer, sample_ir: TableIR, tmp_dir: Path) -> None:
    issues = check_writer_structure(writer) + check_writer_behavior(writer, sample_ir, tmp_dir)
    if issues:
        details = "\n".join(f"  - [{i.check}] {i.detail}" for i in issues)
        raise AssertionError(f"{writer.name} non è conforme al contratto Writer:\n{details}")
