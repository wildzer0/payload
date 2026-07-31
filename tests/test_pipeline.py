from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import NoWriterFoundError, ReaderParseError, SourceNotFoundError
from payload.core.pipeline import build


def test_build_produces_expected_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_path, was_built = build(source_file, registry, config, out_dir, writer_name="fake_writer")

    assert was_built is True
    assert out_path.read_bytes() == b"FAKE:hello table"


def test_build_missing_source_raises(tmp_path, registry, config):
    with pytest.raises(SourceNotFoundError):
        build(tmp_path / "missing.fake", registry, config, tmp_path / "out", writer_name="fake_writer")


def test_build_unknown_writer_raises(source_file, registry, config, tmp_path):
    with pytest.raises(NoWriterFoundError):
        build(source_file, registry, config, tmp_path / "out", writer_name="does_not_exist")


def test_broken_reader_propagates_error(tmp_path, registry, config):
    broken_source = tmp_path / "bad.broken"
    broken_source.write_text("irrilevante")
    with pytest.raises(ReaderParseError):
        build(broken_source, registry, config, tmp_path / "out", writer_name="fake_writer")


def test_cache_skips_second_identical_build(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    _, first_built = build(source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer")
    _, second_built = build(source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer")

    assert first_built is True
    assert second_built is False


def test_cache_invalidated_on_content_change(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    build(source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer")
    source_file.write_text("contenuto diverso")
    _, second_built = build(source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer")

    assert second_built is True


def test_force_bypasses_cache(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    build(source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer")
    _, second_built = build(
        source_file, registry, config, out_dir, cache=cache, writer_name="fake_writer", force=True
    )

    assert second_built is True


def test_dry_run_does_not_write_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_path, was_built = build(
        source_file, registry, config, out_dir, writer_name="fake_writer", dry_run=True
    )

    assert was_built is True
    assert not out_path.exists()
