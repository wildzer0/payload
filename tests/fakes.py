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
