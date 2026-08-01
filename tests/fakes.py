"""
Fake in-memory plugins, used to test pipeline/cache/golden/registry
without ever invoking a real compiler. Massively speeds up the core
test suite and makes it independent of an installed toolchain.
"""
from __future__ import annotations

from pathlib import Path

from payload.core.ir import PLUGIN_API_VERSION, TableIR


class FakeReader:
    """Reads the file as text and simply converts it to utf-8 bytes."""

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
    """Writes the IR's bytes prefixed with a marker, to easily verify
    in tests that the right writer was invoked."""

    name = "fake_writer"
    extension = ".fakeout"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        out_path.write_bytes(b"FAKE:" + ir.data)
        return out_path


class FakeBatchReader:
    """Like FakeReader, but also supports parse_many (batch table, see
    src/payload/docs/BATCH.md): concatenates each file's text content
    in the ORDER GIVEN, separated by '|' to make the order easy to
    check in tests."""

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
    """Reader that always fails — used to test error propagation."""

    name = "broken_reader"
    extensions = [".broken"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        from payload.core.errors import ReaderParseError
        raise ReaderParseError(path, "simulated error")
