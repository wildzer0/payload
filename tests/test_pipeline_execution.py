from pathlib import Path

import pytest

from payload.core.errors import (
    InvalidPipelineError,
    ToolchainExecutionError,
    WriterEmitError,
)
from payload.core.ir import TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry


class _FakeDefaults:
    def __init__(self, writer=None, reader=None):
        self.writer = writer
        self.reader = reader


class _FakeConfig:
    def __init__(self, pipeline_stages=None, writer=None):
        self.defaults = _FakeDefaults(writer)
        self.pipeline_stages = pipeline_stages or []

    def model_dump(self):
        return {"defaults": {"writer": self.defaults.writer}}


class _FakeReader:
    name = "fake_reader"
    extensions = [".fake"]
    api_version = "1.0"
    default_writer = "fake_writer"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        return TableIR(name=path.stem, data=path.read_bytes(), source_path=path, source_format=self.name)


class _FakeWriter:
    name = "fake_writer"
    extension = ".out"
    api_version = "1.0"
    compatible_readers = None

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data)
        return out_path


@pytest.fixture
def registry():
    r = PluginRegistry()
    r.register_reader(_FakeReader())
    r.register_writer(_FakeWriter())
    return r


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "t.fake"
    p.write_text("hello")
    return p


# --- explicit pipeline + explicit --from/--to together -------------------

def test_explicit_pipeline_warns_when_reader_or_writer_name_also_given(tmp_path, source, registry, caplog):
    import logging

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    with caplog.at_level(logging.WARNING):
        build([source], registry, _FakeConfig(stages), tmp_path / "out", writer_name="fake_writer")

    assert any("ignored" in r.message for r in caplog.records)


# --- backward compatibility: implicit 2-stage pipeline --------------------

def test_implicit_pipeline_matches_old_behavior(tmp_path, source, registry):
    out_paths, built = build([source], registry, _FakeConfig(), tmp_path / "out")
    assert built is True
    assert out_paths[0].read_bytes() == b"hello"


def test_implicit_pipeline_cache_works(tmp_path, source, registry):
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    _, built1 = build([source], registry, _FakeConfig(), tmp_path / "out", cache=cache)
    _, built2 = build([source], registry, _FakeConfig(), tmp_path / "out", cache=cache)
    assert built1 is True
    assert built2 is False


# --- explicit multi-stage pipeline with a real exec -----------------------

def test_explicit_three_stage_pipeline_with_exec(tmp_path, registry):
    source = tmp_path / "t.fake"
    source.write_text("HELLO WORLD")

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": 'tr "[:upper:]" "[:lower:]" < {input} > {output}', "output_extension": ".lower"},
    ]
    out_paths, built = build([source], registry, _FakeConfig(stages), tmp_path / "out")

    assert out_paths[0].name == "t.lower"
    assert out_paths[0].read_text() == "hello world"


def test_explicit_five_stage_pipeline(tmp_path, registry):
    source = tmp_path / "t.fake"
    source.write_text("original data")

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo TRANSFORMED >> {input} && cp {input} {output}"},
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    out_paths, built = build([source], registry, _FakeConfig(stages), tmp_path / "out")

    content = out_paths[0].read_text()
    assert "original data" in content
    assert "TRANSFORMED" in content


# --- on_error ---------------------------------------------------------

def test_exec_on_error_fail_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "exit 1", "output_extension": ".x"},
    ]
    with pytest.raises(ToolchainExecutionError):
        build([source], registry, _FakeConfig(stages), tmp_path / "out")


def test_exec_on_error_warn_produces_final_file_outside_tmp(tmp_path, source, registry):
    """Regression: the on_error='warn' fallback must copy the file to
    the expected final location, not leave it inside tmp/ (which gets
    cleaned up at the end of the build and would make it disappear)."""
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "exit 1", "output_extension": ".x", "on_error": "warn"},
    ]
    out_paths, built = build([source], registry, _FakeConfig(stages), tmp_path / "out")

    assert out_paths[0].exists()
    assert out_paths[0].read_bytes() == b"hello"


def test_exec_unknown_placeholder_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {nonexistent_placeholder}", "output_extension": ".x"},
    ]
    with pytest.raises(ToolchainExecutionError):
        build([source], registry, _FakeConfig(stages), tmp_path / "out")


def test_exec_missing_output_file_raises(tmp_path, source, registry):
    """The command returns 0 but doesn't produce the expected file -> clear error."""
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "true", "output_extension": ".x"},  # doesn't touch {output}
    ]
    with pytest.raises(ToolchainExecutionError):
        build([source], registry, _FakeConfig(stages), tmp_path / "out")


# --- whole-pipeline cache --------------------------------------------------

def test_cache_invalidated_by_changing_pipeline(tmp_path, source, registry):
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    p1 = [{"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"}]
    p2 = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    _, built_a = build([source], registry, _FakeConfig(p1), tmp_path / "out", cache=cache)
    _, built_b = build([source], registry, _FakeConfig(p2), tmp_path / "out", cache=cache)

    assert built_a is True
    assert built_b is True  # different pipeline, must not be a cache hit


def test_non_last_exec_stage_persists_checkpoint_when_cache_given(tmp_path, source, registry):
    """An 'exec' stage that is NOT the last one (here followed by
    reader+writer) must persist a checkpoint when a cache is passed —
    explicitly covering this branch, distinct from a terminal 'exec'
    stage (which never gets its own checkpoint, covered by the table
    cache instead)."""
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo TRANSFORMED >> {input} && cp {input} {output}"},
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    out_paths, built = build([source], registry, _FakeConfig(stages), tmp_path / "out", cache=cache)

    assert built is True
    assert "TRANSFORMED" in out_paths[0].read_text()
    checkpoint_dir = tmp_path / "cache" / "stage_artifacts"
    assert any(p.name.startswith("t_stage2") for p in checkpoint_dir.iterdir())


# --- dry-run does not execute exec -----------------------------------------

def test_dry_run_does_not_execute_exec_stage(tmp_path, source, registry):
    marker = tmp_path / "must_not_exist.txt"
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": f"touch {marker}", "output_extension": ".x"},
    ]
    build([source], registry, _FakeConfig(stages), tmp_path / "out", dry_run=True)

    assert not marker.exists()


# --- keep_intermediate ----------------------------------------------------

def test_keep_intermediate_leaves_tmp_dir(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    build([source], registry, _FakeConfig(stages), tmp_path / "out", keep_intermediate=True)

    assert (source.parent / "tmp").exists()


def test_without_keep_intermediate_tmp_dir_is_cleaned(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    build([source], registry, _FakeConfig(stages), tmp_path / "out", keep_intermediate=False)

    assert not (source.parent / "tmp").exists()


# --- validation against the registry (unknown names, compatibility) -------

def test_unknown_reader_name_in_pipeline_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "nonexistent_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    with pytest.raises(InvalidPipelineError):
        build([source], registry, _FakeConfig(stages), tmp_path / "out")


def test_unknown_writer_name_in_pipeline_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "nonexistent_writer"},
    ]
    with pytest.raises(InvalidPipelineError):
        build([source], registry, _FakeConfig(stages), tmp_path / "out")


def test_compatibility_checked_on_second_pair_not_just_first(tmp_path, registry):
    """Reader/writer compatibility must be checked on EVERY pair in
    the pipeline, not just the first."""

    class _ReaderB:
        name = "reader_b"
        extensions = [".b"]
        api_version = "1.0"
        default_writer = None

        def sniff(self, path):
            return False

        def parse(self, path, config):
            return TableIR(name=path.stem, data=b"b", source_path=path, source_format=self.name)

    class _PickyWriter:
        name = "picky"
        extension = ".x"
        api_version = "1.0"
        compatible_readers = ["fake_reader"]  # does NOT accept reader_b

        def emit(self, ir, out_path, config):
            out_path.write_bytes(ir.data)
            return out_path

    registry.register_reader(_ReaderB())
    registry.register_writer(_PickyWriter())

    import tempfile
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "t.fake"
    src.write_text("x")

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},  # OK, compatible with all
        {"type": "reader", "name": "reader_b"},
        {"type": "writer", "name": "picky"},  # NOT compatible with reader_b
    ]
    with pytest.raises(WriterEmitError):
        build([src], registry, _FakeConfig(stages), tmp / "out")
