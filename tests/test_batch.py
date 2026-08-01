from pathlib import Path

from payload.core.batch import run_batch_build
from payload.core.cache import BuildCache
from payload.core.discovery import TableRef
from payload.core.golden import set_golden
from payload.core.history import HistoryStore
from payload.core.registry import PluginRegistry
from tests.fakes import BrokenReader, FakeBatchReader, FakeReader, FakeWriter


def _registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_reader(FakeReader())
    r.register_reader(BrokenReader())
    r.register_writer(FakeWriter())
    return r


def _ref(path: Path) -> TableRef:
    return TableRef(name=path.stem, source_paths=[path], is_batch=False)


def _write_table_refs(tmp_path: Path, n: int) -> list[TableRef]:
    refs = []
    for i in range(n):
        p = tmp_path / f"table{i}.fake"
        p.write_text(f"content {i}")
        refs.append(_ref(p))
    return refs


def test_run_batch_build_aggregates_built_and_cached_counts(tmp_path):
    tables = _write_table_refs(tmp_path, 3)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    first = run_batch_build(tables, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")
    assert first.built == 3
    assert first.cached == 0
    assert first.errors == 0

    second = run_batch_build(tables, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")
    assert second.built == 0
    assert second.cached == 3


def test_run_batch_build_collects_failures_without_raising(tmp_path):
    good = tmp_path / "good.fake"
    good.write_text("ok")
    bad = tmp_path / "bad.broken"
    bad.write_text("irrelevant")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    summary = run_batch_build(
        [_ref(good), _ref(bad)], tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer"
    )

    assert summary.built == 1
    assert summary.errors == 1
    assert len(summary.failures) == 1


def test_run_batch_build_detects_golden_stale_when_source_changed(tmp_path):
    """Golden is a snapshot: if the source changes after golden was
    set, comparing the output is no longer reliable — 'stale' counts
    as a mismatch in the batch build summary."""
    src = tmp_path / "t.fake"
    src.write_text("v1")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    run_batch_build([_ref(src)], tmp_path, registry, cache, out_dir, writer_name="fake_writer")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", [src], [out_dir / "t.fakeout"], "v1")
    set_golden(history, "t", snap.id)

    src.write_text("v2")  # source changed after the golden

    summary = run_batch_build(
        [_ref(src)], tmp_path, registry, cache, out_dir,
        writer_name="fake_writer", check_golden_flag=True, force=True,
    )

    assert summary.built == 1
    assert summary.golden_mismatch == 1


def test_run_batch_build_detects_golden_mismatch_on_tampered_output(tmp_path):
    """Source unchanged but the output on disk differs from what the
    golden snapshot recorded: a real regression."""
    src = tmp_path / "t.fake"
    src.write_text("v1")
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    run_batch_build([_ref(src)], tmp_path, registry, cache, out_dir, writer_name="fake_writer")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", [src], [out_dir / "t.fakeout"], "v1")
    set_golden(history, "t", snap.id)

    (out_dir / "t.fakeout").write_bytes(b"tampered by hand, not by the writer")

    # source unchanged -> cache hit, the build does NOT rewrite the tampered output
    summary = run_batch_build(
        [_ref(src)], tmp_path, registry, cache, out_dir,
        writer_name="fake_writer", check_golden_flag=True,
    )

    assert summary.built == 0
    assert summary.cached == 1
    assert summary.golden_mismatch == 1


def test_run_batch_build_calls_on_table_result_for_each_source(tmp_path):
    tables = _write_table_refs(tmp_path, 3)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")
    seen = []

    run_batch_build(
        tables, tmp_path, registry, cache, tmp_path / "out",
        writer_name="fake_writer", on_table_result=lambda ref, status: seen.append((ref, status)),
    )

    assert len(seen) == 3
    assert all(status == "ok" for _, status in seen)
    assert all(isinstance(ref, TableRef) for ref, _ in seen)


def test_run_batch_build_saves_cache_once(tmp_path):
    tables = _write_table_refs(tmp_path, 2)
    registry = _registry()
    cache = BuildCache(tmp_path / "cache")

    run_batch_build(tables, tmp_path, registry, cache, tmp_path / "out", writer_name="fake_writer")

    assert cache.path.exists()
    # a new instance loaded from the same dir sees the cache already saved on disk
    reloaded_cache = BuildCache(tmp_path / "cache")
    second = run_batch_build(tables, tmp_path, registry, reloaded_cache, tmp_path / "out", writer_name="fake_writer")
    assert second.built == 0
    assert second.cached == 2


def test_run_batch_build_batch_table_uses_effective_config_overrides(tmp_path):
    """A batch table (is_batch=True) must resolve reader/writer from
    the [[batch_table]] overrides, not from the global config — same
    mechanism as effective_config() used by the CLI."""
    from payload.core.batch_tables import BatchTable

    row1 = tmp_path / "ROW1.fakebatch"
    row2 = tmp_path / "ROW2.fakebatch"
    row1.write_text("one")
    row2.write_text("two")
    (tmp_path / "table-tool.toml").touch()
    registry = _registry()
    registry.register_reader(FakeBatchReader())
    cache = BuildCache(tmp_path / "cache")

    bt = BatchTable(name="rows", source_paths=[row1, row2], writer="fake_writer")
    ref = TableRef(name="rows", source_paths=[row1, row2], is_batch=True, batch=bt)

    summary = run_batch_build([ref], tmp_path, registry, cache, tmp_path / "out")

    assert summary.built == 1
    assert summary.errors == 0
    assert (tmp_path / "out" / "rows.fakeout").exists()
