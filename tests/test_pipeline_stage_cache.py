"""
Test per la cache per singolo stage: uno stage writer/exec non finale
deve poter essere riusato tra una build e l'altra se il suo prefisso
di pipeline (e il sorgente/config) non è cambiato — anche se stage
successivi sono diversi.
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


class _FakeConfig:
    def __init__(self, stages):
        self.defaults = _FakeDefaults()
        self.pipeline_stages = stages

    def model_dump(self):
        return {"defaults": {"writer": None}}


class _CountingReader:
    """Reader che conta quante volte parse() viene invocato — per
    verificare che un checkpoint successivo eviti davvero di richiamarlo."""

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
    p.write_text("dato")
    return p


def test_changing_last_stage_reuses_earlier_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")

    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build(source, registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo altro > {output}", "output_extension": ".v2"},
    ]
    build(source, registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    # il reader NON deve essere stato richiamato una seconda volta:
    # il checkpoint dopo lo stage 'writer' viene riusato
    assert _CountingReader.call_count == 1


def test_force_bypasses_stage_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build(source, registry, _FakeConfig(stages), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    build(source, registry, _FakeConfig(stages), tmp_path / "out", cache=cache, force=True)
    assert _CountingReader.call_count == 2


def test_changing_source_invalidates_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build(source, registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)
    assert _CountingReader.call_count == 1

    source.write_text("dato modificato")

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo altro > {output}", "output_extension": ".v2"},
    ]
    build(source, registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    # il sorgente e' cambiato: il checkpoint NON deve essere riusato
    assert _CountingReader.call_count == 2


def test_output_is_correct_when_resuming_from_checkpoint(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build(source, registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "printf 'MODIFICATO' > {output}", "output_extension": ".v2"},
    ]
    out_paths, built = build(source, registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    assert built is True
    assert out_paths[0].read_text() == "MODIFICATO"


def test_stage_checkpoint_survives_across_builds_after_tmp_cleanup(tmp_path, source, registry):
    """Il checkpoint vive FUORI da tmp/ (che viene ripulita ad ogni
    build) — deve sopravvivere anche se tmp/ non esiste più."""
    cache = BuildCache(tmp_path / "cache")
    stages_v1 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".v1"},
    ]
    build(source, registry, _FakeConfig(stages_v1), tmp_path / "out", cache=cache)

    # tmp/ NON deve esistere piu' (ripulita a fine build, comportamento di default)
    assert not (source.parent / "tmp").exists()

    stages_v2 = [
        {"type": "reader", "name": "counting_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo altro > {output}", "output_extension": ".v2"},
    ]
    build(source, registry, _FakeConfig(stages_v2), tmp_path / "out", cache=cache)

    assert _CountingReader.call_count == 1  # checkpoint riusato comunque
