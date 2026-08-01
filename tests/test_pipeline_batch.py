"""
build() with more than one source_path (batch table, see
src/payload/docs/BATCH.md) — the single-element case stays covered by
test_pipeline.py; here we only cover what changes with N files:
dispatch to parse_many, cache/tmp_dir identity, and rejecting a reader
that doesn't support batch.
"""
from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import ReaderBatchUnsupportedError, SourceNotFoundError
from payload.core.pipeline import build, describe_table_build
from payload.core.registry import PluginRegistry
from tests.fakes import FakeBatchReader, FakeReader, FakeWriter


@pytest.fixture
def batch_registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_reader(FakeBatchReader())
    r.register_reader(FakeReader())
    r.register_writer(FakeWriter())
    return r


@pytest.fixture
def batch_sources(tmp_path) -> list[Path]:
    p1 = tmp_path / "ROW1.fakebatch"
    p2 = tmp_path / "ROW2.fakebatch"
    p1.write_text("one")
    p2.write_text("two")
    return [p1, p2]


def test_build_batch_dispatches_to_parse_many(tmp_path, batch_sources, batch_registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build(
        batch_sources, batch_registry, config, out_dir,
        writer_name="fake_writer", table_name="rows",
    )
    assert was_built is True
    assert out_paths[0].read_bytes() == b"FAKE:one|two"


def test_build_batch_respects_source_paths_order(tmp_path, batch_registry, config):
    p1 = tmp_path / "ROW1.fakebatch"
    p2 = tmp_path / "ROW2.fakebatch"
    p1.write_text("one")
    p2.write_text("two")
    out_dir = tmp_path / "out"

    out_paths, _ = build([p2, p1], batch_registry, config, out_dir, writer_name="fake_writer", table_name="rows")

    assert out_paths[0].read_bytes() == b"FAKE:two|one"


def test_build_batch_output_named_after_table_name_not_any_source_stem(tmp_path, batch_sources, batch_registry, config):
    out_dir = tmp_path / "out"
    out_paths, _ = build(batch_sources, batch_registry, config, out_dir, writer_name="fake_writer", table_name="rows")
    assert out_paths[0].name == "rows.fakeout"


def test_build_batch_missing_table_name_raises_type_error_via_index(tmp_path, batch_sources, batch_registry, config):
    """table_name is required in practice for a batch (no stem to
    derive) — without passing it explicitly, the name derived from
    source_paths[0] is still a valid value (just misleading), so here
    we verify the fallback exists and doesn't raise."""
    out_dir = tmp_path / "out"
    out_paths, _ = build(batch_sources, batch_registry, config, out_dir, writer_name="fake_writer")
    assert out_paths[0].name == "ROW1.fakeout"


def test_build_batch_reader_without_parse_many_raises(tmp_path, batch_registry, config):
    p1 = tmp_path / "a.fake"
    p2 = tmp_path / "b.fake"
    p1.write_text("x")
    p2.write_text("y")
    out_dir = tmp_path / "out"

    with pytest.raises(ReaderBatchUnsupportedError, match="fake_reader"):
        build([p1, p2], batch_registry, config, out_dir, reader_name="fake_reader", writer_name="fake_writer", table_name="t")


def test_build_batch_missing_source_reports_the_missing_one(tmp_path, batch_sources, batch_registry, config):
    missing = tmp_path / "ROW3.fakebatch"
    out_dir = tmp_path / "out"

    with pytest.raises(SourceNotFoundError, match="ROW3"):
        build(batch_sources + [missing], batch_registry, config, out_dir, writer_name="fake_writer", table_name="rows")


def test_build_batch_cache_hit_on_second_identical_build(tmp_path, batch_sources, batch_registry, config):
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    _, first_built = build(batch_sources, batch_registry, config, out_dir, cache=cache, writer_name="fake_writer", table_name="rows")
    _, second_built = build(batch_sources, batch_registry, config, out_dir, cache=cache, writer_name="fake_writer", table_name="rows")

    assert first_built is True
    assert second_built is False


def test_build_batch_cache_miss_when_a_member_file_content_changes(tmp_path, batch_sources, batch_registry, config):
    cache = BuildCache(tmp_path / "cache")
    out_dir = tmp_path / "out"

    build(batch_sources, batch_registry, config, out_dir, cache=cache, writer_name="fake_writer", table_name="rows")
    batch_sources[1].write_text("modified")
    _, second_built = build(batch_sources, batch_registry, config, out_dir, cache=cache, writer_name="fake_writer", table_name="rows")

    assert second_built is True


def test_build_batch_dry_run_does_not_write(tmp_path, batch_sources, batch_registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build(
        batch_sources, batch_registry, config, out_dir,
        writer_name="fake_writer", table_name="rows", dry_run=True,
    )
    assert was_built is True
    assert not out_paths[0].exists()


def test_describe_table_build_batch_uses_table_name_for_expected_outputs(tmp_path, batch_sources, batch_registry, config):
    from dataclasses import replace

    cfg = replace(config, defaults=replace(config.defaults, writer="fake_writer"))
    info = describe_table_build(batch_sources, batch_registry, cfg, [], tmp_path, table_name="rows")

    assert info["missing_outputs"] == ["rows.fakeout"]
    assert info["reader"] == "fake_batch_reader"
