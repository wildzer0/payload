from dataclasses import replace
from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import NoWriterFoundError, ReaderParseError, SourceNotFoundError, WriterEmitError
from payload.core.pipeline import build, describe_table_build
from tests.fakes import FakeWriter


def test_build_produces_expected_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build([source_file], registry, config, out_dir, writer_name="fake_writer")

    assert was_built is True
    assert out_paths[0].read_bytes() == b"FAKE:hello table"


def test_build_missing_source_raises(tmp_path, registry, config):
    with pytest.raises(SourceNotFoundError):
        build([tmp_path / "missing.fake"], registry, config, tmp_path / "out", writer_name="fake_writer")


def test_build_unknown_writer_raises(source_file, registry, config, tmp_path):
    with pytest.raises(NoWriterFoundError):
        build([source_file], registry, config, tmp_path / "out", writer_name="does_not_exist")


def test_broken_reader_propagates_error(tmp_path, registry, config):
    broken_source = tmp_path / "bad.broken"
    broken_source.write_text("irrilevante")
    with pytest.raises(ReaderParseError):
        build([broken_source], registry, config, tmp_path / "out", writer_name="fake_writer")


def test_reader_raising_unexpected_exception_is_wrapped(tmp_path, registry, config):
    """Regression found by a user: an incomplete plugin (e.g. a
    local_plugin scaffold that was never finished, with 'raise
    NotImplementedError' instead of real parsing) must not produce a
    raw traceback in front of the user — it must become a readable
    ReaderParseError, with the original exception preserved as the
    cause for whoever debugs it."""
    class HalfBakedReader:
        name = "half_baked"
        extensions = [".half"]
        api_version = "1.0"

        def sniff(self, path):
            return False

        def parse(self, path, cfg):
            raise NotImplementedError("TODO: implement parsing")

    registry.register_reader(HalfBakedReader())
    source = tmp_path / "t.half"
    source.write_text("something")

    with pytest.raises(ReaderParseError) as exc_info:
        build([source], registry, config, tmp_path / "out", writer_name="fake_writer")

    assert "half_baked" in exc_info.value.message
    assert "NotImplementedError" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, NotImplementedError)


def test_writer_raising_unexpected_exception_is_wrapped(tmp_path, source_file, registry, config):
    class HalfBakedWriter:
        name = "half_baked_writer"
        extension = ".half"
        api_version = "1.0"

        def emit(self, ir, out_path, cfg):
            raise NotImplementedError("TODO: implement emit")

    registry.register_writer(HalfBakedWriter())

    with pytest.raises(WriterEmitError) as exc_info:
        build([source_file], registry, config, tmp_path / "out", writer_name="half_baked_writer")

    assert "half_baked_writer" in exc_info.value.message
    assert "NotImplementedError" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, NotImplementedError)


def test_cache_skips_second_identical_build(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    _, first_built = build([source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer")
    _, second_built = build([source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer")

    assert first_built is True
    assert second_built is False


def test_cache_invalidated_on_content_change(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    build([source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer")
    source_file.write_text("different content")
    _, second_built = build([source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer")

    assert second_built is True


def test_force_bypasses_cache(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    cache = BuildCache(tmp_path / "cache")

    build([source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer")
    _, second_built = build(
        [source_file], registry, config, out_dir, cache=cache, writer_name="fake_writer", force=True
    )

    assert second_built is True


def test_build_removes_stale_output_from_a_previous_writer(tmp_path, source_file, registry, config):
    """Regression found by a user: building with one writer, then
    rebuilding the SAME table with a different writer (even just an
    ad-hoc override via --to, never written to config) must not leave
    the previous writer's output lying around — otherwise a later
    commit would reabsorb it as if it were still part of the table's
    current state."""
    class OtherWriter:
        name = "other_writer"
        extension = ".other"
        api_version = FakeWriter.api_version

        def emit(self, ir, out_path, cfg):
            out_path.write_bytes(b"OTHER:" + ir.data)
            return out_path

    registry.register_writer(OtherWriter())
    out_dir = tmp_path / "out"

    out_paths_a, _ = build([source_file], registry, config, out_dir, writer_name="fake_writer")
    assert out_paths_a[0].exists()

    out_paths_b, _ = build([source_file], registry, config, out_dir, writer_name="other_writer")

    assert out_paths_b[0].exists()
    assert not out_paths_a[0].exists()


def test_build_dry_run_does_not_remove_stale_outputs(tmp_path, source_file, registry, config):
    class OtherWriter:
        name = "other_writer"
        extension = ".other"
        api_version = FakeWriter.api_version

        def emit(self, ir, out_path, cfg):
            out_path.write_bytes(b"OTHER:" + ir.data)
            return out_path

    registry.register_writer(OtherWriter())
    out_dir = tmp_path / "out"

    out_paths_a, _ = build([source_file], registry, config, out_dir, writer_name="fake_writer")
    build([source_file], registry, config, out_dir, writer_name="other_writer", dry_run=True)

    assert out_paths_a[0].exists()


def test_describe_table_build_simple_reader_writer(tmp_path, source_file, registry, config):
    cfg = replace(config, defaults=replace(config.defaults, writer="fake_writer"))
    out_path = tmp_path / "example.fakeout"
    out_path.write_bytes(b"whatever")

    info = describe_table_build([source_file], registry, cfg, [out_path], tmp_path)

    assert info["reader"] == "fake_reader"
    assert info["writers"] == ["fake_writer"]
    assert info["pipeline_explicit"] is False
    assert info["pipeline_description"] is None
    assert info["missing_outputs"] == []


def test_describe_table_build_explicit_pipeline(tmp_path, source_file, registry, config):
    cfg = replace(config, pipeline_stages=[
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ])
    out_path = tmp_path / "example.fakeout"
    out_path.write_bytes(b"whatever")

    info = describe_table_build([source_file], registry, cfg, [out_path], tmp_path)

    assert info["pipeline_explicit"] is True
    assert info["pipeline_description"] == "reader:fake_reader -> writer:fake_writer"


def test_describe_table_build_unresolvable_pipeline_falls_back(tmp_path, source_file, registry, config):
    """Without a resolvable writer (no --to, no default in config, the
    fake reader doesn't suggest one) resolve_pipeline_spec raises —
    describe_table_build must not propagate the error, the real build
    will fail further down anyway with the same message."""
    info = describe_table_build([source_file], registry, config, [], tmp_path)

    assert info["reader"] is None
    assert info["pipeline_description"] is None
    assert info["writers"] == []
    assert info["missing_outputs"] == []


def test_describe_table_build_reports_missing_output_from_partial_fanout(tmp_path, source_file, registry, config):
    """Regression: if a writer in the fan-out configured RIGHT NOW
    hasn't produced its own file (e.g. a partial FanOutWriteError, or
    simply not built yet), the commit must be able to flag it instead
    of letting it pass unnoticed."""
    cfg = replace(config, pipeline_stages=[
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ])
    # no .fakeout file on disk: the writer the pipeline expects
    # hasn't (yet) produced anything.
    info = describe_table_build([source_file], registry, cfg, [], tmp_path)

    assert info["missing_outputs"] == ["example.fakeout"]


def test_describe_table_build_infers_writer_from_output_extension_not_config(tmp_path, source_file, registry, config):
    """Regression found by a user: the reported writer must reflect
    the file REALLY committed, not the config — an ad-hoc override
    (--to) used for a single build is never written to config, so
    resolving from config would have shown the wrong writer."""
    class OtherWriter:
        name = "other_writer"
        extension = ".other"
        api_version = FakeWriter.api_version

        def emit(self, ir, out_path, cfg):
            out_path.write_bytes(b"OTHER")
            return out_path

    registry.register_writer(OtherWriter())
    cfg = replace(config, defaults=replace(config.defaults, writer="fake_writer"))
    out_path = tmp_path / "example.other"
    out_path.write_bytes(b"built with other_writer, not the config default")

    info = describe_table_build([source_file], registry, cfg, [out_path], tmp_path)

    assert info["writers"] == ["other_writer"]


def test_dry_run_does_not_write_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build(
        [source_file], registry, config, out_dir, writer_name="fake_writer", dry_run=True
    )

    assert was_built is True
    assert not out_paths[0].exists()
