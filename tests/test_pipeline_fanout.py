"""
Fan-out: un reader seguito da più writer consecutivi (tutti terminali)
riceve la STESSA IR, parsata una sola volta. Vedi docs/PIPELINE.md,
sezione Fan-out.
"""
from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import WriterEmitError
from payload.core.ir import TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry


class _FakeDefaults:
    writer = None


class _FakeConfig:
    def __init__(self, pipeline_stages):
        self.defaults = _FakeDefaults()
        self.pipeline_stages = pipeline_stages

    def model_dump(self):
        return {"defaults": {"writer": None}}


class _CountingReader:
    """Conta quante volte parse() viene invocato — il punto reale del
    fan-out è che un reader costoso gira una sola volta anche con N
    writer a valle."""

    name = "counting_reader"
    extensions = [".fake"]
    api_version = "1.0"
    default_writer = None
    call_count = 0

    def sniff(self, path):
        return False

    def parse(self, path, config):
        type(self).call_count += 1
        return TableIR(name=path.stem, data=path.read_bytes(), source_path=path, source_format=self.name)


def _make_writer(writer_name: str, ext: str, compatible=None):
    class _Writer:
        name = writer_name
        extension = ext
        api_version = "1.0"
        compatible_readers = compatible
        called = False

        def emit(self, ir, out_path, config):
            type(self).called = True
            out_path.write_bytes(ir.data + writer_name.encode())
            return out_path

    return _Writer


@pytest.fixture(autouse=True)
def _reset_counting_reader():
    _CountingReader.call_count = 0
    yield


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "t.fake"
    p.write_text("dati")
    return p


@pytest.fixture
def registry():
    r = PluginRegistry()
    r.register_reader(_CountingReader())
    r.register_writer(_make_writer("bin", ".bin")())
    r.register_writer(_make_writer("hex", ".hex")())
    r.register_writer(_make_writer("header", ".h")())
    return r


def _fanout_stages(*writer_names):
    return [{"type": "reader", "name": "counting_reader"}] + [
        {"type": "writer", "name": n} for n in writer_names
    ]


def test_fan_out_writes_n_files_with_distinct_extensions(tmp_path, source, registry):
    out_paths, built = build(
        source, registry, _FakeConfig(_fanout_stages("bin", "hex", "header")), tmp_path / "out"
    )

    assert built is True
    assert {p.suffix for p in out_paths} == {".bin", ".hex", ".h"}
    for p in out_paths:
        assert p.exists()
        assert p.read_bytes().startswith(b"dati")


def test_fan_out_reader_parse_called_exactly_once(tmp_path, source, registry):
    build(source, registry, _FakeConfig(_fanout_stages("bin", "hex", "header")), tmp_path / "out")

    assert _CountingReader.call_count == 1


def test_fan_out_cache_hit_on_second_identical_build(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    config = _FakeConfig(_fanout_stages("bin", "hex"))

    _, first_built = build(source, registry, config, tmp_path / "out", cache=cache)
    out_paths, second_built = build(source, registry, config, tmp_path / "out", cache=cache)

    assert first_built is True
    assert second_built is False
    assert all(p.exists() for p in out_paths)


def test_fan_out_cache_miss_if_one_of_n_outputs_deleted(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    config = _FakeConfig(_fanout_stages("bin", "hex"))

    out_paths, _ = build(source, registry, config, tmp_path / "out", cache=cache)
    out_paths[1].unlink()

    _, second_built = build(source, registry, config, tmp_path / "out", cache=cache)

    assert second_built is True


def test_fan_out_writer_incompatibility_checked_for_every_writer_in_group(tmp_path, source, registry):
    picky = _make_writer("picky", ".picky", compatible=["altro_reader"])()
    registry.register_writer(picky)

    with pytest.raises(WriterEmitError):
        build(source, registry, _FakeConfig(_fanout_stages("bin", "picky")), tmp_path / "out")

    assert picky.called is False
