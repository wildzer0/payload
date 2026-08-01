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
    p.write_text("ciao")
    return p


# --- pipeline esplicita + --from/--to esplicito insieme -------------------

def test_explicit_pipeline_warns_when_reader_or_writer_name_also_given(tmp_path, source, registry, caplog):
    import logging

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    with caplog.at_level(logging.WARNING):
        build(source, registry, _FakeConfig(stages), tmp_path / "out", writer_name="fake_writer")

    assert any("ignorati" in r.message for r in caplog.records)


# --- retrocompatibilità: pipeline implicita a 2 stage --------------------

def test_implicit_pipeline_matches_old_behavior(tmp_path, source, registry):
    out_paths, built = build(source, registry, _FakeConfig(), tmp_path / "out")
    assert built is True
    assert out_paths[0].read_bytes() == b"ciao"


def test_implicit_pipeline_cache_works(tmp_path, source, registry):
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    _, built1 = build(source, registry, _FakeConfig(), tmp_path / "out", cache=cache)
    _, built2 = build(source, registry, _FakeConfig(), tmp_path / "out", cache=cache)
    assert built1 is True
    assert built2 is False


# --- pipeline esplicita multi-stage con exec reale ------------------------

def test_explicit_three_stage_pipeline_with_exec(tmp_path, registry):
    source = tmp_path / "t.fake"
    source.write_text("CIAO MONDO")

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": 'tr "[:upper:]" "[:lower:]" < {input} > {output}', "output_extension": ".lower"},
    ]
    out_paths, built = build(source, registry, _FakeConfig(stages), tmp_path / "out")

    assert out_paths[0].name == "t.lower"
    assert out_paths[0].read_text() == "ciao mondo"


def test_explicit_five_stage_pipeline(tmp_path, registry):
    source = tmp_path / "t.fake"
    source.write_text("dato originale")

    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo TRASFORMATO >> {input} && cp {input} {output}"},
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    out_paths, built = build(source, registry, _FakeConfig(stages), tmp_path / "out")

    content = out_paths[0].read_text()
    assert "dato originale" in content
    assert "TRASFORMATO" in content


# --- on_error ---------------------------------------------------------

def test_exec_on_error_fail_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "exit 1", "output_extension": ".x"},
    ]
    with pytest.raises(ToolchainExecutionError):
        build(source, registry, _FakeConfig(stages), tmp_path / "out")


def test_exec_on_error_warn_produces_final_file_outside_tmp(tmp_path, source, registry):
    """Regressione: il fallback di on_error='warn' deve copiare il file
    nella posizione finale attesa, non lasciarlo dentro tmp/ (che viene
    ripulita a fine build e lo farebbe sparire)."""
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "exit 1", "output_extension": ".x", "on_error": "warn"},
    ]
    out_paths, built = build(source, registry, _FakeConfig(stages), tmp_path / "out")

    assert out_paths[0].exists()
    assert out_paths[0].read_bytes() == b"ciao"


def test_exec_unknown_placeholder_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {placeholder_inesistente}", "output_extension": ".x"},
    ]
    with pytest.raises(ToolchainExecutionError):
        build(source, registry, _FakeConfig(stages), tmp_path / "out")


def test_exec_missing_output_file_raises(tmp_path, source, registry):
    """Il comando ritorna 0 ma non produce il file atteso -> errore chiaro."""
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "true", "output_extension": ".x"},  # non tocca {output}
    ]
    with pytest.raises(ToolchainExecutionError):
        build(source, registry, _FakeConfig(stages), tmp_path / "out")


# --- cache sull'intera pipeline ------------------------------------------

def test_cache_invalidated_by_changing_pipeline(tmp_path, source, registry):
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    p1 = [{"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"}]
    p2 = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    _, built_a = build(source, registry, _FakeConfig(p1), tmp_path / "out", cache=cache)
    _, built_b = build(source, registry, _FakeConfig(p2), tmp_path / "out", cache=cache)

    assert built_a is True
    assert built_b is True  # pipeline diversa, non deve essere un cache hit


def test_non_last_exec_stage_persists_checkpoint_when_cache_given(tmp_path, source, registry):
    """Uno stage 'exec' che NON è l'ultimo (qui seguito da reader+writer)
    deve persistere un checkpoint quando una cache è passata — coprire
    esplicitamente questo ramo, distinto dallo stage 'exec' terminale
    (che non riceve mai un checkpoint proprio, coperto dalla cache di
    tabella)."""
    from payload.core.cache import BuildCache

    cache = BuildCache(tmp_path / "cache")
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "echo TRASFORMATO >> {input} && cp {input} {output}"},
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ]
    out_paths, built = build(source, registry, _FakeConfig(stages), tmp_path / "out", cache=cache)

    assert built is True
    assert "TRASFORMATO" in out_paths[0].read_text()
    checkpoint_dir = tmp_path / "cache" / "stage_artifacts"
    assert any(p.name.startswith("t_stage2") for p in checkpoint_dir.iterdir())


# --- dry-run non esegue exec ----------------------------------------------

def test_dry_run_does_not_execute_exec_stage(tmp_path, source, registry):
    marker = tmp_path / "non_deve_esistere.txt"
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": f"touch {marker}", "output_extension": ".x"},
    ]
    build(source, registry, _FakeConfig(stages), tmp_path / "out", dry_run=True)

    assert not marker.exists()


# --- keep_intermediate ----------------------------------------------------

def test_keep_intermediate_leaves_tmp_dir(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    build(source, registry, _FakeConfig(stages), tmp_path / "out", keep_intermediate=True)

    assert (source.parent / "tmp").exists()


def test_without_keep_intermediate_tmp_dir_is_cleaned(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"}, {"type": "writer", "name": "fake_writer"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".x"},
    ]
    build(source, registry, _FakeConfig(stages), tmp_path / "out", keep_intermediate=False)

    assert not (source.parent / "tmp").exists()


# --- validazione contro il registry (nomi sconosciuti, compatibilità) ----

def test_unknown_reader_name_in_pipeline_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "reader_inesistente"},
        {"type": "writer", "name": "fake_writer"},
    ]
    with pytest.raises(InvalidPipelineError):
        build(source, registry, _FakeConfig(stages), tmp_path / "out")


def test_unknown_writer_name_in_pipeline_raises(tmp_path, source, registry):
    stages = [
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "writer_inesistente"},
    ]
    with pytest.raises(InvalidPipelineError):
        build(source, registry, _FakeConfig(stages), tmp_path / "out")


def test_compatibility_checked_on_second_pair_not_just_first(tmp_path, registry):
    """La compatibilità reader/writer va verificata su OGNI coppia della
    pipeline, non solo sulla prima."""

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
        compatible_readers = ["fake_reader"]  # NON accetta reader_b

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
        {"type": "writer", "name": "fake_writer"},  # OK, compatibile con tutti
        {"type": "reader", "name": "reader_b"},
        {"type": "writer", "name": "picky"},  # NON compatibile con reader_b
    ]
    with pytest.raises(WriterEmitError):
        build(src, registry, _FakeConfig(stages), tmp / "out")
