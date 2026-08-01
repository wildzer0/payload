from dataclasses import replace
from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.errors import NoWriterFoundError, ReaderParseError, SourceNotFoundError
from payload.core.pipeline import build, describe_table_build
from tests.fakes import FakeWriter


def test_build_produces_expected_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build(source_file, registry, config, out_dir, writer_name="fake_writer")

    assert was_built is True
    assert out_paths[0].read_bytes() == b"FAKE:hello table"


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


def test_build_removes_stale_output_from_a_previous_writer(tmp_path, source_file, registry, config):
    """Regressione trovata dall'utente: costruire con un writer, poi
    ricostruire la STESSA tabella con un writer diverso (anche solo
    un override ad-hoc via --to, mai scritto in config) non deve
    lasciare in giro l'output del writer precedente — altrimenti un
    commit successivo lo riassorbirebbe come se facesse ancora parte
    dello stato attuale della tabella."""
    class OtherWriter:
        name = "other_writer"
        extension = ".other"
        api_version = FakeWriter.api_version

        def emit(self, ir, out_path, cfg):
            out_path.write_bytes(b"OTHER:" + ir.data)
            return out_path

    registry.register_writer(OtherWriter())
    out_dir = tmp_path / "out"

    out_paths_a, _ = build(source_file, registry, config, out_dir, writer_name="fake_writer")
    assert out_paths_a[0].exists()

    out_paths_b, _ = build(source_file, registry, config, out_dir, writer_name="other_writer")

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

    out_paths_a, _ = build(source_file, registry, config, out_dir, writer_name="fake_writer")
    build(source_file, registry, config, out_dir, writer_name="other_writer", dry_run=True)

    assert out_paths_a[0].exists()


def test_describe_table_build_simple_reader_writer(tmp_path, source_file, registry, config):
    cfg = replace(config, defaults=replace(config.defaults, writer="fake_writer"))
    out_path = tmp_path / "example.fakeout"
    out_path.write_bytes(b"whatever")

    info = describe_table_build(source_file, registry, cfg, [out_path], tmp_path)

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

    info = describe_table_build(source_file, registry, cfg, [out_path], tmp_path)

    assert info["pipeline_explicit"] is True
    assert info["pipeline_description"] == "reader:fake_reader -> writer:fake_writer"


def test_describe_table_build_unresolvable_pipeline_falls_back(tmp_path, source_file, registry, config):
    """Senza writer risolvibile (nessun --to, nessun default in
    config, il reader fake non ne suggerisce uno) resolve_pipeline_spec
    solleva — describe_table_build non deve propagare l'errore, la
    build vera fallirà comunque più avanti con lo stesso messaggio."""
    info = describe_table_build(source_file, registry, config, [], tmp_path)

    assert info["reader"] is None
    assert info["pipeline_description"] is None
    assert info["writers"] == []
    assert info["missing_outputs"] == []


def test_describe_table_build_reports_missing_output_from_partial_fanout(tmp_path, source_file, registry, config):
    """Regressione: se un writer del fan-out configurato ORA non ha
    prodotto il proprio file (es. un FanOutWriteError parziale, o
    semplicemente non ancora buildato), il commit deve poterlo
    segnalare invece di lasciarlo passare inosservato."""
    cfg = replace(config, pipeline_stages=[
        {"type": "reader", "name": "fake_reader"},
        {"type": "writer", "name": "fake_writer"},
    ])
    # nessun file .fakeout su disco: il writer atteso dalla pipeline
    # non ha (ancora) prodotto nulla.
    info = describe_table_build(source_file, registry, cfg, [], tmp_path)

    assert info["missing_outputs"] == ["example.fakeout"]


def test_describe_table_build_infers_writer_from_output_extension_not_config(tmp_path, source_file, registry, config):
    """Regressione trovata dall'utente: il writer riportato deve
    riflettere il file REALMENTE committato, non la config — un
    override ad-hoc (--to) usato per una singola build non viene mai
    scritto in config, quindi risolvere dalla config avrebbe mostrato
    il writer sbagliato."""
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

    info = describe_table_build(source_file, registry, cfg, [out_path], tmp_path)

    assert info["writers"] == ["other_writer"]


def test_dry_run_does_not_write_output(tmp_path, source_file, registry, config):
    out_dir = tmp_path / "out"
    out_paths, was_built = build(
        source_file, registry, config, out_dir, writer_name="fake_writer", dry_run=True
    )

    assert was_built is True
    assert not out_paths[0].exists()
