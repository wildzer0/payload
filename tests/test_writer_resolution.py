from pathlib import Path

import pytest

from payload.core.errors import WriterEmitError, WriterNotSpecifiedError
from payload.core.ir import TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry


class _FakeDefaults:
    def __init__(self, writer=None):
        self.writer = writer


class _FakeConfig:
    def __init__(self, writer=None):
        self.defaults = _FakeDefaults(writer)
        self.pipeline_stages = []

    def model_dump(self):
        return {"defaults": {"writer": self.defaults.writer}}


class _ReaderWithDefault:
    name = "reader_with_default"
    extensions = [".wd"]
    api_version = "1.0"
    default_writer = "generic"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        return TableIR(name=path.stem, data=b"x", source_path=path, source_format=self.name)


class _ReaderNoDefault:
    name = "reader_no_default"
    extensions = [".nd"]
    api_version = "1.0"
    default_writer = None

    def sniff(self, path):
        return False

    def parse(self, path, config):
        return TableIR(name=path.stem, data=b"x", source_path=path, source_format=self.name)


class _GenericWriter:
    name = "generic"
    extension = ".out"
    api_version = "1.0"
    compatible_readers = None

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data)
        return out_path


class _PickyWriter:
    name = "picky"
    extension = ".picky"
    api_version = "1.0"
    compatible_readers = ["reader_with_default"]

    def __init__(self):
        self.called = False

    def emit(self, ir, out_path, config):
        self.called = True
        out_path.write_bytes(ir.data)
        return out_path


@pytest.fixture
def registry():
    r = PluginRegistry()
    r.register_reader(_ReaderWithDefault())
    r.register_reader(_ReaderNoDefault())
    r.register_writer(_GenericWriter())
    r.register_writer(_PickyWriter())
    return r


def test_uses_reader_default_writer_when_nothing_else_specified(tmp_path, registry):
    src = tmp_path / "t.wd"
    src.write_text("x")

    out_paths, built = build(src, registry, _FakeConfig(), tmp_path / "out")

    assert out_paths[0].suffix == ".out"


def test_explicit_to_wins_over_reader_default(tmp_path, registry):
    src = tmp_path / "t.wd"
    src.write_text("x")

    out_paths, built = build(src, registry, _FakeConfig(), tmp_path / "out", writer_name="picky")

    assert out_paths[0].suffix == ".picky"


def test_config_writer_wins_over_reader_default(tmp_path, registry):
    src = tmp_path / "t.wd"
    src.write_text("x")

    out_paths, built = build(src, registry, _FakeConfig(writer="picky"), tmp_path / "out")

    assert out_paths[0].suffix == ".picky"


def test_no_writer_resolvable_raises_clear_error(tmp_path, registry):
    src = tmp_path / "t.nd"
    src.write_text("x")

    with pytest.raises(WriterNotSpecifiedError):
        build(src, registry, _FakeConfig(), tmp_path / "out")


def test_incompatible_writer_rejected_before_parsing(tmp_path, registry):
    src = tmp_path / "t.nd"  # reader_no_default, NON compatibile con 'picky'
    src.write_text("x")
    picky = registry.writers["picky"]

    with pytest.raises(WriterEmitError):
        build(src, registry, _FakeConfig(), tmp_path / "out", writer_name="picky")

    assert picky.called is False  # emit() non deve mai essere invocato


def test_compatible_writer_accepted(tmp_path, registry):
    src = tmp_path / "t.wd"  # reader_with_default, COMPATIBILE con 'picky'
    src.write_text("x")

    out_paths, built = build(src, registry, _FakeConfig(), tmp_path / "out", writer_name="picky")

    assert out_paths[0].suffix == ".picky"
