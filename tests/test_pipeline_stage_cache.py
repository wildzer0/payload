"""
Tests for per-stage caching: a non-final writer/exec stage must be
reusable across builds if its pipeline prefix (and the source/config)
hasn't changed — even if the later stages differ.
"""
import logging

import pytest

from payload.core.cache import BuildCache
from payload.core.ir import TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry


class _FakeDefaults:
    def __init__(self):
        self.writer = None
        self.reader = None


class _FakeConfig:
    def __init__(self, stages):
        self.defaults = _FakeDefaults()
        self.pipeline_stages = stages

    def model_dump(self):
        return {"defaults": {"writer": None}}


class _CountingReader:
    """Reader that counts how many times parse() is invoked — to
    verify that a later checkpoint really avoids calling it again."""

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


class _FakeWriter:
    name = "fake_writer"
    extension = ".bin"
    api_version = "1.0"
    compatible_readers = None

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data)
        return out_path


@pytest.fixture
def registry():
    _CountingReader.call_count = 0
    r = PluginRegistry()
    r.register_reader(_CountingReader())
    r.register_writer(_FakeWriter())
    return r


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "t.fake"
    p.write_text("data")
    return p


def test_changing_last_stage_reuses_earlier_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")

    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build([source], registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo other > {output}", "output_extension": ".v2"},
    ]
    build([source], registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    # the reader must NOT have been called a second time: the
    # checkpoint after the 'writer' stage is reused
    assert _CountingReader.call_count == 1


def test_force_bypasses_stage_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build([source], registry, _FakeConfig(stages), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    build([source], registry, _FakeConfig(stages), tmp_path / "out", cache=cache, force=True)
    assert _CountingReader.call_count == 2


def test_changing_source_invalidates_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build([source], registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    source.write_text("modified data")

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo other > {output}", "output_extension": ".v2"},
    ]
    build([source], registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    # the source changed: the checkpoint must NOT be reused
    assert _CountingReader.call_count == 2


def test_output_is_correct_when_resuming_from_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build([source], registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "printf 'MODIFIED' > {output}", "output_extension": ".v2"},
    ]
    out_paths, built = build([source], registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    assert built is True
    assert out_paths[0].read_text() == "MODIFIED"


def test_stage_checkpoint_survives_across_builds_after_tmp_cleanup(tmp_path, source, registry):
    """The checkpoint lives OUTSIDE tmp/ (which gets cleaned up on
    every build) — it must survive even if tmp/ no longer exists."""
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build([source], registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)

    # tmp/ must NOT exist anymore (cleaned up at the end of the build, default behavior)
    assert not (source.parent / "tmp").exists()

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo other > {output}", "output_extension": ".v2"},
    ]
    build([source], registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    assert _CountingReader.call_count == 1  # checkpoint still reused
