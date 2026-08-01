"""
Plugin fake in-memory, usati per testare pipeline/cache/golden/registry
senza mai invocare un vero compilatore. Velocizza enormemente la test
suite del core e la rende indipendente da un toolchain installato.
"""
from __future__ import annotations

from pathlib import Path

from payload.core.ir import PLUGIN_API_VERSION, TableIR


class FakeReader:
    """Legge il file come testo e lo converte semplicemente in bytes utf-8."""

    name = "fake_reader"
    extensions = [".fake"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        content = path.read_text()
        return TableIR(
            name=path.stem,
            data=content.encode(),
            source_path=path,
            source_format=self.name,
        )


class FakeWriter:
    """Scrive i bytes della IR preceduti da un marker, per verificare
    facilmente nei test che il writer giusto sia stato invocato."""

    name = "fake_writer"
    extension = ".fakeout"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        out_path.write_bytes(b"FAKE:" + ir.data)
        return out_path


class FakeBatchReader:
    """Come FakeReader, ma supporta anche parse_many (batch table, vedi
    src/payload/docs/BATCH.md): concatena il contenuto testuale di ogni
    file NELL'ORDINE DATO, separato da '|' per rendere l'ordine
    verificabile facilmente nei test."""

    name = "fake_batch_reader"
    extensions = [".fakebatch"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        return TableIR(
            name=path.stem, data=path.read_text().encode(),
            source_path=path, source_format=self.name,
        )

    def parse_many(self, paths: list[Path], config: dict) -> TableIR:
        content = "|".join(p.read_text() for p in paths)
        return TableIR(
            name=paths[0].stem, data=content.encode(),
            source_path=paths[0], source_format=self.name,
        )


class BrokenReader:
    """Reader che fallisce sempre — usato per testare la propagazione errori."""

    name = "broken_reader"
    extensions = [".broken"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        from payload.core.errors import ReaderParseError
        raise ReaderParseError(path, "errore simulato")
