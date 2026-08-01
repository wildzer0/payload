from pathlib import Path

from payload.core.batch import run_batch_build
from payload.core.cache import BuildCache
from payload.core.golden import set_golden
from payload.core.history import HistoryStore
from payload.core.registry import PluginRegistry
from tests.fakes import BrokenReader, FakeReader, FakeWriter


def _registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_reader(FakeReader())
    r.register_reader(BrokenReader())
    r.register_writer(FakeWriter())
    return r


def _write_sources(tmp_path: Path, n: int) -> list[Path]:
    sources = []
    for i in range(n):
        p = tmp_path / f"table{i}.fake"
        p.write_text(f"contenuto {i}")
        sources.append(p)
    return sources


def test_run_batch_build_aggregates_built_and_cached_counts(tmp_path):
    sources = _write_sources(tmp_path, 3)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    first = run_batch_build(sources, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")
    assert first.built == 3
    assert first.cached == 0
    assert first.errors == 0

    second = run_batch_build(sources, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")
    assert second.built == 0
    assert second.cached == 3


def test_run_batch_build_collects_failures_without_raising(tmp_path):
    good = tmp_path / "good.fake"
    good.write_text("ok")
    bad = tmp_path / "bad.broken"
    bad.write_text("irrilevante")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    summary = run_batch_build(
        [good, bad], tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer"
    )

    assert summary.built == 1
    assert summary.errors == 1
    assert len(summary.failures) == 1


def test_run_batch_build_detects_golden_stale_when_source_changed(tmp_path):
    """Golden è uno snapshot: se il sorgente cambia dopo che golden è
    stato impostato, il confronto sull'output non è più affidabile —
    'stale' conta come mismatch nel riepilogo del batch build."""
    src = tmp_path / "t.fake"
    src.write_text("v1")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    run_batch_build([src], tmp_path, registry, cache, out_dir, writer_name="fake_writer")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", src, [out_dir / "t.fakeout"], "v1")
    set_golden(history, "t", snap.id)

    src.write_text("v2")  # sorgente cambiato dopo il golden

    summary = run_batch_build(
        [src], tmp_path, registry, cache, out_dir,
        writer_name="fake_writer", check_golden_flag=True, force=True,
    )

    assert summary.built == 1
    assert summary.golden_mismatch == 1


def test_run_batch_build_detects_golden_mismatch_on_tampered_output(tmp_path):
    """Sorgente invariato ma l'output su disco è diverso da quello che
    lo snapshot golden aveva registrato: una vera regressione."""
    src = tmp_path / "t.fake"
    src.write_text("v1")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    run_batch_build([src], tmp_path, registry, cache, out_dir, writer_name="fake_writer")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", src, [out_dir / "t.fakeout"], "v1")
    set_golden(history, "t", snap.id)

    (out_dir / "t.fakeout").write_bytes(b"manomesso a mano, non dal writer")

    # sorgente invariato -> cache hit, il build NON riscrive l'output manomesso
    summary = run_batch_build(
        [src], tmp_path, registry, cache, out_dir,
        writer_name="fake_writer", check_golden_flag=True,
    )

    assert summary.built == 0
    assert summary.cached == 1
    assert summary.golden_mismatch == 1


def test_run_batch_build_calls_on_table_result_for_each_source(tmp_path):
    sources = _write_sources(tmp_path, 3)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    seen = []

    run_batch_build(
        sources, tmp_path, registry, cache, tmp_path / "out",
        writer_name="fake_writer", on_table_result=lambda src, status: seen.append((src, status)),
    )

    assert len(seen) == 3
    assert all(status == "ok" for _, status in seen)


def test_run_batch_build_saves_cache_once(tmp_path):
    sources = _write_sources(tmp_path, 2)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    run_batch_build(sources, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")

    assert cache.path.exists()
    # una nuova istanza caricata dallo stesso dir vede la cache già salvata su disco
    reloaded_cache = BuildCache(tmp_path / "cache")
    second = run_batch_build(sources, tmp_path, registry, reloaded_cache, tmp_path / "out", writer_name="fake_writer")
    assert second.built == 0
    assert second.cached == 2
