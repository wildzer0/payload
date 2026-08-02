from pathlib import Path

import pytest

from payload.core.clusters import resolve_clusters
from payload.core.config import (
    DefaultsConfig,
    config_schema,
    create_cluster,
    delete_cluster,
    delete_sidecar_config,
    load_config,
    read_raw_sidecar,
    remove_table_meta_entry,
    set_table_cluster,
    set_table_tags,
    update_cluster,
    write_global_config,
    write_sidecar_config,
)
from payload.core.errors import ClusterError, InvalidConfigError
from payload.core.table_meta import resolve_table_meta


def _defaults(**overrides) -> dict:
    return {**DefaultsConfig().__dict__, **overrides}


# --- write_global_config ---------------------------------------------------


def test_write_global_config_round_trips(tmp_path):
    write_global_config(tmp_path, _defaults(writer="hex"))

    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"


def test_write_global_config_preserves_plugin_and_pipeline_sections(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "bin"\n'
        '[plugin.custom]\nkey = "value"\n'
        '[pipeline]\nstages = [{ type = "reader", name = "raw_text" }, { type = "writer", name = "bin" }]\n'
    )

    write_global_config(tmp_path, _defaults(writer="hex"))

    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"
    assert c.plugin == {"custom": {"key": "value"}}
    assert len(c.pipeline_stages) == 2


def test_write_global_config_rejects_unknown_field_without_writing(tmp_path):
    path = tmp_path / "table-tool.toml"
    with pytest.raises(InvalidConfigError):
        write_global_config(tmp_path, {**_defaults(), "made_up_field": 1})
    assert not path.exists()


def test_write_global_config_rejects_invalid_byte_order(tmp_path):
    with pytest.raises(InvalidConfigError):
        write_global_config(tmp_path, _defaults(byte_order="middle"))


def test_write_global_config_rejects_stray_toolchain_section(tmp_path):
    """[toolchain] isn't part of the core schema anymore (see
    core/config.py) — compiler/objcopy settings now live under
    [plugin.<name>] instead, owned by whichever plugin needs them."""
    (tmp_path / "table-tool.toml").write_text('[toolchain]\ncompiler = "gcc"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_write_global_config_overwrites_previous_values(tmp_path):
    write_global_config(tmp_path, _defaults(writer="hex"))
    write_global_config(tmp_path, _defaults(writer="bin"))

    assert load_config(tmp_path).defaults.writer == "bin"


# --- sidecar -----------------------------------------------------------


def _source(tmp_path: Path) -> Path:
    src_dir = tmp_path / "sensors"
    src_dir.mkdir()
    src = src_dir / "temp.raw"
    src.write_text("x")
    return src


def test_read_raw_sidecar_missing_returns_empty_dict(tmp_path):
    src = _source(tmp_path)
    assert read_raw_sidecar(src) == {}


def test_write_sidecar_config_round_trips_defaults(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"})

    assert read_raw_sidecar(src) == {"defaults": {"writer": "hex"}}
    c = load_config(tmp_path, source_path=src)
    assert c.defaults.writer == "hex"


def test_write_sidecar_config_with_pipeline_stages(tmp_path):
    src = _source(tmp_path)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    write_sidecar_config(src, pipeline_stages=stages)

    c = load_config(tmp_path, source_path=src)
    assert c.pipeline_stages == stages


def test_write_sidecar_config_none_leaves_other_sections_untouched(tmp_path):
    src = _source(tmp_path)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    write_sidecar_config(src, defaults={"writer": "hex"})
    write_sidecar_config(src, pipeline_stages=stages)

    raw = read_raw_sidecar(src)
    assert raw["defaults"] == {"writer": "hex"}
    assert raw["pipeline"] == {"stages": stages}


def test_write_sidecar_config_empty_dict_removes_only_that_section(tmp_path):
    src = _source(tmp_path)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    write_sidecar_config(src, defaults={"writer": "hex"}, pipeline_stages=stages)
    write_sidecar_config(src, defaults={})

    raw = read_raw_sidecar(src)
    assert "defaults" not in raw
    assert raw["pipeline"] == {"stages": stages}


def test_write_sidecar_config_empty_pipeline_stages_removes_only_that_section(tmp_path):
    src = _source(tmp_path)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    write_sidecar_config(src, defaults={"writer": "hex"}, pipeline_stages=stages)
    write_sidecar_config(src, pipeline_stages=[])

    raw = read_raw_sidecar(src)
    assert raw["defaults"] == {"writer": "hex"}
    assert "pipeline" not in raw


def test_write_sidecar_config_fully_empty_deletes_file(tmp_path):
    src = _source(tmp_path)
    sidecar_path = write_sidecar_config(src, defaults={"writer": "hex"})
    assert sidecar_path.exists()

    write_sidecar_config(src, defaults={})

    assert not sidecar_path.exists()


def test_write_sidecar_config_drops_none_defaults_values(tmp_path):
    src = _source(tmp_path)
    # reflects the web form: a field with the "override" switch on but
    # left empty arrives as None, it must not break validation (it
    # must be treated as "not set").
    write_sidecar_config(src, defaults={"writer": "hex", "reader": None})

    raw = read_raw_sidecar(src)
    assert raw["defaults"] == {"writer": "hex"}


def test_write_sidecar_config_preserves_plugin_section(tmp_path):
    src = _source(tmp_path)
    (src.parent / "temp.config.toml").write_text('[plugin.custom]\nkey = "value"\n')

    write_sidecar_config(src, defaults={"writer": "hex"})

    raw = read_raw_sidecar(src)
    assert raw["plugin"] == {"custom": {"key": "value"}}
    assert raw["defaults"] == {"writer": "hex"}


def test_write_sidecar_config_rejects_unknown_field(tmp_path):
    src = _source(tmp_path)
    with pytest.raises(InvalidConfigError):
        write_sidecar_config(src, defaults={"made_up_field": 1})


def test_write_sidecar_config_rejects_invalid_byte_order(tmp_path):
    src = _source(tmp_path)
    with pytest.raises(InvalidConfigError):
        write_sidecar_config(src, defaults={"byte_order": "middle"})


def test_delete_sidecar_config(tmp_path):
    src = _source(tmp_path)
    write_sidecar_config(src, defaults={"writer": "hex"})

    assert delete_sidecar_config(src) is True
    assert delete_sidecar_config(src) is False  # idempotent


# --- config_schema -----------------------------------------------------


def test_config_schema_lists_known_fields_with_types():
    schema = config_schema()

    defaults_keys = {f["key"] for f in schema["defaults"]}
    assert defaults_keys == {"writer", "reader", "output_dir", "cache_dir", "byte_order"}
    assert all(f["type"] == "string" for f in schema["defaults"])
    assert "toolchain" not in schema


# --- create_cluster / update_cluster / delete_cluster -----------------------


def test_create_cluster_writes_new_entry(tmp_path):
    create_cluster(tmp_path, "sensors", defaults={"writer": "hex"})

    config = load_config(tmp_path)
    assert config.clusters == [{"name": "sensors", "defaults": {"writer": "hex"}}]


def test_create_cluster_with_no_overrides(tmp_path):
    create_cluster(tmp_path, "sensors")

    config = load_config(tmp_path)
    assert config.clusters == [{"name": "sensors"}]


def test_create_cluster_with_plugin_override(tmp_path):
    create_cluster(tmp_path, "sensors", plugin={"c_source": {"compiler": "gcc"}})

    config = load_config(tmp_path)
    assert config.clusters[0]["plugin"] == {"c_source": {"compiler": "gcc"}}


def test_create_cluster_duplicate_name_raises(tmp_path):
    create_cluster(tmp_path, "sensors")
    with pytest.raises(ClusterError):
        create_cluster(tmp_path, "sensors")


def test_create_cluster_preserves_existing_defaults(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "hex"\n')
    create_cluster(tmp_path, "sensors")
    config = load_config(tmp_path)
    assert config.defaults.writer == "hex"
    assert len(config.clusters) == 1


def test_update_cluster_replaces_defaults(tmp_path):
    create_cluster(tmp_path, "sensors", defaults={"writer": "hex"})

    update_cluster(tmp_path, "sensors", defaults={"writer": "bin", "output_dir": "build/x"})

    config = load_config(tmp_path)
    assert config.clusters[0]["defaults"] == {"writer": "bin", "output_dir": "build/x"}


def test_update_cluster_none_leaves_section_untouched(tmp_path):
    create_cluster(tmp_path, "sensors", defaults={"writer": "hex"}, plugin={"c_source": {"compiler": "gcc"}})

    update_cluster(tmp_path, "sensors", defaults=None, plugin=None)

    config = load_config(tmp_path)
    assert config.clusters[0]["defaults"] == {"writer": "hex"}
    assert config.clusters[0]["plugin"] == {"c_source": {"compiler": "gcc"}}


def test_update_cluster_empty_dict_clears_section(tmp_path):
    create_cluster(tmp_path, "sensors", defaults={"writer": "hex"})

    update_cluster(tmp_path, "sensors", defaults={})

    config = load_config(tmp_path)
    assert "defaults" not in config.clusters[0]


def test_update_cluster_sets_plugin_section(tmp_path):
    create_cluster(tmp_path, "sensors")

    update_cluster(tmp_path, "sensors", plugin={"c_source": {"compiler": "gcc"}})

    config = load_config(tmp_path)
    assert config.clusters[0]["plugin"] == {"c_source": {"compiler": "gcc"}}


def test_update_cluster_empty_plugin_clears_section(tmp_path):
    create_cluster(tmp_path, "sensors", plugin={"c_source": {"compiler": "gcc"}})

    update_cluster(tmp_path, "sensors", plugin={})

    config = load_config(tmp_path)
    assert "plugin" not in config.clusters[0]


def test_update_cluster_unknown_name_raises(tmp_path):
    with pytest.raises(ClusterError):
        update_cluster(tmp_path, "does_not_exist", defaults={"writer": "hex"})


def test_delete_cluster_removes_entry(tmp_path):
    create_cluster(tmp_path, "sensors")

    removed = delete_cluster(tmp_path, "sensors")

    assert removed is True
    config = load_config(tmp_path)
    assert config.clusters == []


def test_delete_cluster_no_file_returns_false(tmp_path):
    assert delete_cluster(tmp_path, "sensors") is False


def test_delete_cluster_unknown_name_returns_false(tmp_path):
    create_cluster(tmp_path, "sensors")
    assert delete_cluster(tmp_path, "does_not_exist") is False


def test_delete_cluster_with_members_refuses_without_force(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")

    with pytest.raises(ClusterError):
        delete_cluster(tmp_path, "sensors")

    config = load_config(tmp_path)
    assert config.clusters != []


def test_delete_cluster_with_force_clears_members_keeps_tags(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")
    set_table_tags(tmp_path, "t1", ["prod"])

    removed = delete_cluster(tmp_path, "sensors", force=True)

    assert removed is True
    config = load_config(tmp_path)
    assert config.clusters == []
    assert config.table_meta == [{"name": "t1", "tags": ["prod"]}]


def test_delete_cluster_with_force_drops_entry_with_no_tags_left(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")

    delete_cluster(tmp_path, "sensors", force=True)

    config = load_config(tmp_path)
    assert config.table_meta == []


# --- set_table_cluster / set_table_tags / remove_table_meta_entry ----------


def test_set_table_cluster_creates_entry(tmp_path):
    create_cluster(tmp_path, "sensors")

    set_table_cluster(tmp_path, "t1", "sensors")

    config = load_config(tmp_path)
    assert config.table_meta == [{"name": "t1", "cluster": "sensors"}]


def test_set_table_cluster_unknown_cluster_raises(tmp_path):
    with pytest.raises(ClusterError):
        set_table_cluster(tmp_path, "t1", "does_not_exist")


def test_set_table_cluster_clear_removes_field(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")

    set_table_cluster(tmp_path, "t1", None)

    config = load_config(tmp_path)
    assert config.table_meta == []  # entry auto-removed: nothing left to declare


def test_set_table_cluster_clear_keeps_tags(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")
    set_table_tags(tmp_path, "t1", ["prod"])

    set_table_cluster(tmp_path, "t1", None)

    config = load_config(tmp_path)
    assert config.table_meta == [{"name": "t1", "tags": ["prod"]}]


def test_set_table_cluster_reassign_replaces_previous(tmp_path):
    create_cluster(tmp_path, "a")
    create_cluster(tmp_path, "b")
    set_table_cluster(tmp_path, "t1", "a")

    set_table_cluster(tmp_path, "t1", "b")

    config = load_config(tmp_path)
    assert config.table_meta[0]["cluster"] == "b"


def test_set_table_tags_creates_entry(tmp_path):
    set_table_tags(tmp_path, "t1", ["prod", "beta"])

    config = load_config(tmp_path)
    assert config.table_meta == [{"name": "t1", "tags": ["prod", "beta"]}]


def test_set_table_tags_deduplicates_preserving_order(tmp_path):
    set_table_tags(tmp_path, "t1", ["a", "b", "a", "c", "b"])

    config = load_config(tmp_path)
    assert config.table_meta[0]["tags"] == ["a", "b", "c"]


def test_set_table_tags_empty_list_removes_entry(tmp_path):
    set_table_tags(tmp_path, "t1", ["a"])

    set_table_tags(tmp_path, "t1", [])

    config = load_config(tmp_path)
    assert config.table_meta == []


def test_set_table_tags_empty_list_keeps_cluster(tmp_path):
    create_cluster(tmp_path, "sensors")
    set_table_cluster(tmp_path, "t1", "sensors")
    set_table_tags(tmp_path, "t1", ["a"])

    set_table_tags(tmp_path, "t1", [])

    config = load_config(tmp_path)
    assert config.table_meta == [{"name": "t1", "cluster": "sensors"}]


def test_set_table_tags_replaces_whole_list(tmp_path):
    set_table_tags(tmp_path, "t1", ["a", "b"])

    set_table_tags(tmp_path, "t1", ["c"])

    config = load_config(tmp_path)
    assert config.table_meta[0]["tags"] == ["c"]


def test_remove_table_meta_entry_removes_whole_block(tmp_path):
    set_table_tags(tmp_path, "t1", ["a"])

    removed = remove_table_meta_entry(tmp_path, "t1")

    assert removed is True
    config = load_config(tmp_path)
    assert config.table_meta == []


def test_remove_table_meta_entry_no_file_returns_false(tmp_path):
    assert remove_table_meta_entry(tmp_path, "t1") is False


def test_remove_table_meta_entry_unknown_name_returns_false(tmp_path):
    set_table_tags(tmp_path, "t1", ["a"])
    assert remove_table_meta_entry(tmp_path, "does_not_exist") is False


def test_cluster_and_table_meta_crud_round_trip_via_resolve(tmp_path):
    create_cluster(tmp_path, "sensors", defaults={"writer": "hex"})
    set_table_cluster(tmp_path, "t1", "sensors")
    set_table_tags(tmp_path, "t1", ["prod"])

    config = load_config(tmp_path)
    clusters = resolve_clusters(tmp_path, config)
    metas = resolve_table_meta(tmp_path, config, clusters)

    assert metas["t1"].cluster == "sensors"
    assert metas["t1"].tags == ["prod"]
    assert clusters["sensors"].defaults == {"writer": "hex"}




def _init_project(tmp_path):
    from typer.testing import CliRunner

    from payload.cli import app as cli_app

    runner = CliRunner()
    root = tmp_path / "proj"
    result = runner.invoke(cli_app, ["init", str(root)])
    assert result.exit_code == 0, result.stdout
    return root


def test_meta_notes_and_properties_roundtrip(tmp_path):
    from payload.core.config import set_table_meta_fields, set_table_tags
    from payload.core.config import load_config
    from payload.core.table_meta import resolve_table_meta

    root = _init_project(tmp_path)
    # notes + properties, alongside existing tags
    set_table_tags(root, "example_table", ["raw"])
    set_table_meta_fields(root, "example_table", notes="calibrated at boot", properties={"address": "0x8000", "version": "2"})

    base = load_config(root)
    meta = resolve_table_meta(root, base)
    assert meta["example_table"].notes == "calibrated at boot"
    assert meta["example_table"].properties == {"address": "0x8000", "version": "2"}
    assert meta["example_table"].tags == ["raw"]

    # clearing everything drops the [[table_meta]] entry entirely
    set_table_meta_fields(root, "example_table", notes="", properties={})
    set_table_tags(root, "example_table", [])
    base = load_config(root)
    assert resolve_table_meta(root, base) == {}
    raw = (root / "table-tool.toml").read_text(encoding="utf-8")
    assert "example_table" not in raw


def test_meta_properties_are_injected_into_reader_config(tmp_path):
    from payload.core.config import set_table_meta_fields
    from payload.core.config import load_config
    from payload.core.ir import TableIR
    from payload.core.registry import load_plugins
    from payload.core.pipeline import build

    root = _init_project(tmp_path)
    set_table_meta_fields(root, "example_table", notes="n", properties={"address": "0x8000"})

    source_path = root / "example_table.raw"
    config = load_config(root)
    out_dir = root / "build"
    out_dir.mkdir(exist_ok=True)
    registry = load_plugins(project_root=root)

    captured = {}

    class FakeReader:
        name = "fake_reader"
        extensions = [".raw"]
        api_version = "1.0"
        default_writer = None
        compatible_readers = None

        def __init__(self):  # pragma: no cover - test helper
            pass

        def sniff(self, path):  # pragma: no cover - test helper
            return False

        def parse(self, path, cfg):
            captured["cfg"] = cfg
            return TableIR(name="example_table", data=b"x", source_path=path, source_format="raw_text")

    registry.register_reader(FakeReader())

    build(
        [source_path], registry, config, out_dir,
        reader_name="fake_reader", writer_name=None, force=True, table_name="example_table",
    )
    assert captured["cfg"]["table_meta"]["notes"] == "n"
    assert captured["cfg"]["table_meta"]["properties"] == {"address": "0x8000"}
    assert captured["cfg"]["table_meta"]["cluster"] is None


def test_meta_properties_must_be_dict_of_strings(tmp_path):
    from payload.core.config import load_config

    root = _init_project(tmp_path)
    (root / "table-tool.toml").write_text(
        '[[table_meta]]\nname = "example_table"\nproperties = { bad = 1 }\n',
        encoding="utf-8",
    )
    with pytest.raises(Exception) as exc:
        load_config(root)
    assert "dict of strings" in str(exc.value)




def test_report_html_includes_warnings(tmp_path):
    from payload.core.report import render_report_html

    root = _init_project(tmp_path)
    # two files with the same stem → duplicate-stem problem warning
    (root / "dupe.raw").write_text("# a\n", encoding="utf-8")
    (root / "dupe.txt").write_text("# b\n", encoding="utf-8")
    body = render_report_html(root)
    assert "Project needs attention" in body
