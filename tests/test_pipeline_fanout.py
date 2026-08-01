"""
Fan-out: un reader seguito da più writer consecutivi (tutti terminali)
riceve la STESSA IR, parsata una sola volta. Vedi src/payload/docs/PIPELINE.md,
sezione Fan-out.
"""
from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import FanOutWriteError, WriterEmitError
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


def _make_failing_writer(writer_name: str, ext: str, reason: str = "boom"):
    class _FailingWriter:
        name = writer_name
        extension = ext
        api_version = "1.0"
        compatible_readers = None
        called = False

        def emit(self, ir, out_path, config):
            type(self).called = True
            raise WriterEmitError(writer_name, reason)

    return _FailingWriter


def _make_crashing_writer(writer_name: str, ext: str):
    """A differenza di _make_failing_writer, questo simula un plugin
    incompleto/buggy che solleva un'eccezione GREZZA (non della
    gerarchia PayloadError) — es. uno scaffold mai finito."""
    class _CrashingWriter:
        name = writer_name
        extension = ext
        api_version = "1.0"
        compatible_readers = None
        called = False

        def emit(self, ir, out_path, config):
            type(self).called = True
            raise NotImplementedError("TODO: implementa l'emit")

    return _CrashingWriter


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
        [source], registry, _FakeConfig(_fanout_stages("bin", "hex", "header")), tmp_path / "out"
    )

    assert built is True
    assert {p.suffix for p in out_paths} == {".bin", ".hex", ".h"}
    for p in out_paths:
        assert p.exists()
        assert p.read_bytes().startswith(b"dati")


def test_fan_out_reader_parse_called_exactly_once(tmp_path, source, registry):
    build([source], registry, _FakeConfig(_fanout_stages("bin", "hex", "header")), tmp_path / "out")

    assert _CountingReader.call_count == 1


def test_fan_out_cache_hit_on_second_identical_build(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    config = _FakeConfig(_fanout_stages("bin", "hex"))

    _, first_built = build([source], registry, config, tmp_path / "out", cache=cache)
    out_paths, second_built = build([source], registry, config, tmp_path / "out", cache=cache)

    assert first_built is True
    assert second_built is False
    assert all(p.exists() for p in out_paths)


def test_fan_out_cache_miss_if_one_of_n_outputs_deleted(tmp_path, source, registry):
    cache = BuildCache(tmp_path / "cache")
    config = _FakeConfig(_fanout_stages("bin", "hex"))

    out_paths, _ = build([source], registry, config, tmp_path / "out", cache=cache)
    out_paths[1].unlink()

    _, second_built = build([source], registry, config, tmp_path / "out", cache=cache)

    assert second_built is True


def test_fan_out_writer_incompatibility_checked_for_every_writer_in_group(tmp_path, source, registry):
    picky = _make_writer("picky", ".picky", compatible=["altro_reader"])()
    registry.register_writer(picky)

    with pytest.raises(WriterEmitError):
        build([source], registry, _FakeConfig(_fanout_stages("bin", "picky")), tmp_path / "out")

    assert picky.called is False


def test_fan_out_partial_failure_still_writes_successful_outputs(tmp_path, source, registry):
    """Regressione trovata dall'utente: con 3 writer di cui 1 fallisce
    a runtime, gli altri 2 devono comunque scrivere il proprio output —
    altrimenti l'utente non ha modo di sapere che 2/3 sono riusciti."""
    registry.register_writer(_make_failing_writer("broken", ".broken", "reason simulata")())
    out_dir = tmp_path / "out"

    with pytest.raises(FanOutWriteError) as exc_info:
        build([source], registry, _FakeConfig(_fanout_stages("bin", "broken", "hex")), out_dir)

    err = exc_info.value
    assert (out_dir / "t.bin").exists()
    assert (out_dir / "t.hex").exists()
    assert not (out_dir / "t.broken").exists()
    assert err.context["succeeded_outputs"] == [str(out_dir / "t.bin"), str(out_dir / "t.hex")]
    assert err.context["failed_writers"] == [{"writer": "broken", "reason": "Writer 'broken' non può generare output: reason simulata"}]
    assert "t.bin, t.hex" in err.message
    assert "broken" in err.message


def test_fan_out_all_writers_fail_reports_empty_succeeded(tmp_path, source, registry):
    registry.register_writer(_make_failing_writer("broken1", ".b1")())
    registry.register_writer(_make_failing_writer("broken2", ".b2")())
    out_dir = tmp_path / "out"

    with pytest.raises(FanOutWriteError) as exc_info:
        build([source], registry, _FakeConfig(_fanout_stages("broken1", "broken2")), out_dir)

    assert exc_info.value.context["succeeded_outputs"] == []
    assert len(exc_info.value.context["failed_writers"]) == 2


def test_single_terminal_writer_failure_is_not_wrapped(tmp_path, source, registry):
    """Un solo writer terminale (niente fan-out) non ha nulla di
    'parziale' da riportare: deve fallire esattamente come prima,
    senza il wrapping introdotto per il caso fan-out."""
    registry.register_writer(_make_failing_writer("broken", ".broken")())

    with pytest.raises(WriterEmitError):
        build([source], registry, _FakeConfig(_fanout_stages("broken")), tmp_path / "out")


def test_fan_out_writer_raising_raw_exception_is_captured_not_crashing(tmp_path, source, registry):
    """Regressione trovata dall'utente: un writer del fan-out che
    solleva un'eccezione GREZZA (plugin incompleto, non PayloadError)
    deve finire in failed_writers con un messaggio leggibile, non far
    esplodere l'intera build con un traceback crudo."""
    registry.register_writer(_make_crashing_writer("half_baked", ".half")())
    out_dir = tmp_path / "out"

    with pytest.raises(FanOutWriteError) as exc_info:
        build([source], registry, _FakeConfig(_fanout_stages("bin", "half_baked")), out_dir)

    assert (out_dir / "t.bin").exists()
    failed = exc_info.value.context["failed_writers"]
    assert failed == [{"writer": "half_baked", "reason": "NotImplementedError: TODO: implementa l'emit"}]


def test_single_writer_raising_raw_exception_is_wrapped_in_writer_emit_error(tmp_path, source, registry):
    registry.register_writer(_make_crashing_writer("half_baked", ".half")())

    with pytest.raises(WriterEmitError) as exc_info:
        build([source], registry, _FakeConfig(_fanout_stages("half_baked")), tmp_path / "out")

    assert "half_baked" in exc_info.value.message
    assert "NotImplementedError" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, NotImplementedError)
