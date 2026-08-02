from pathlib import Path

import pytest

from payload.core.config import (
    PayloadConfig,
    add_batch_table_source,
    create_batch_table,
    load_config,
    remove_batch_table_entry,
    remove_batch_table_source,
    resolve_config_with_provenance,
)
from payload.core.errors import BatchTableError, ClusterError, InvalidConfigError, TableMetaError


def test_default_config_has_sensible_values():
    c = PayloadConfig()
    assert c.defaults.writer is None  # no preference: the reader may suggest one


def test_model_dump_returns_plain_dict():
    c = PayloadConfig()
    d = c.model_dump()
    assert d == {
        "project": {"name": "", "description": ""},
        "defaults": {
            "writer": None, "reader": None, "output_dir": "build",
            "cache_dir": ".payload_cache", "byte_order": "little",
        },
        "plugin": {},
        "pipeline_stages": [],
        "batch_tables": [],
        "clusters": [],
        "table_meta": [],
    }


def test_load_config_no_file_uses_defaults(tmp_path):
    c = load_config(tmp_path)
    assert c.defaults.writer is None


def test_load_config_reads_global_toml(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "hex"\n')
    c = load_config(tmp_path)
    assert c.defaults.writer == "hex"


def test_sidecar_overrides_only_declared_keys(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "bin"\n[plugin.c_source]\ncompiler = "gcc"\n'
    )
    src_dir = tmp_path / "sensors"
    src_dir.mkdir()
    src = src_dir / "temp.raw"
    src.write_text("x")
    (src_dir / "temp.config.toml").write_text('[defaults]\nwriter = "hex"\n')

    c = load_config(tmp_path, source_path=src)
    assert c.defaults.writer == "hex"                          # override from the sidecar
    assert c.plugin == {"c_source": {"compiler": "gcc"}}        # inherited from global, untouched by the sidecar


def test_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nmade_up_field = 1\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_wrong_type_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = "not a list"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_malformed_toml_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text("this is not TOML [[[")
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_unknown_top_level_section_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[random_section]\nx = 1\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_str_field_wrong_type_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text("[defaults]\nwriter = 123\n")
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_defaults_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('defaults = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_project_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('project = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_project_name_and_description_loaded(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[project]\nname = "Sensor Calibration"\ndescription = "test bench data acquisition"\n')
    c = load_config(tmp_path)
    assert c.project.name == "Sensor Calibration"
    assert c.project.description == "test bench data acquisition"


def test_toolchain_key_no_longer_recognized(tmp_path):
    """[toolchain] used to be core schema; it's now unknown at the top
    level regardless of shape — compiler/objcopy settings live under
    [plugin.<name>] instead (owned by whichever plugin needs them, see
    examples/plugins/c_source.py, obj_writer.py). A project migrating
    from before this change needs to move a stray [toolchain] section,
    not just fix its type."""
    (tmp_path / "table-tool.toml").write_text('[toolchain]\ncompiler = "gcc"\n')
    with pytest.raises(InvalidConfigError) as exc_info:
        load_config(tmp_path)
    assert "unknown section" in str(exc_info.value)


def test_pipeline_section_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('pipeline = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_pipeline_stages_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[pipeline]\nstages = "not a list"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_invalid_byte_order_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nbyte_order = "middle"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


# --- [[batch_table]] -----------------------------------------------------


def test_batch_table_parsed_into_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )
    config = load_config(tmp_path)
    assert config.batch_tables == [{"name": "rows", "sources": ["ROW*.txt"]}]


def test_batch_table_with_overrides_and_stages(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\n'
        'name = "rows"\n'
        'sources = ["ROW1.txt", "ROW2.txt"]\n'
        'reader = "raw_text"\n'
        'writer = "bin"\n'
        'byte_order = "big"\n'
        'stages = [{ type = "reader", name = "raw_text" }, { type = "writer", name = "bin" }]\n'
    )
    config = load_config(tmp_path)
    assert config.batch_tables[0]["reader"] == "raw_text"
    assert config.batch_tables[0]["byte_order"] == "big"
    assert len(config.batch_tables[0]["stages"]) == 2


def test_batch_table_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('batch_table = "not a list"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_missing_name_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[batch_table]]\nsources = ["ROW*.txt"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_missing_sources_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[batch_table]]\nname = "rows"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\nbogus = "x"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_stages_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\nstages = "not a list"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_batch_table_sources_not_a_list_of_strings_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[batch_table]]\nname = "rows"\nsources = [1, 2]\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_no_batch_table_defaults_to_empty_list(tmp_path):
    config = load_config(tmp_path)
    assert config.batch_tables == []


def test_batch_table_entry_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('batch_table = ["not a table"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


# --- mutazione [[batch_table]] (core/table_admin.py) -----------------------

def test_create_batch_table_writes_new_entry(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt", "ROW2.txt"])

    config = load_config(tmp_path)
    assert config.batch_tables == [{"name": "rows", "sources": ["ROW1.txt", "ROW2.txt"]}]


def test_create_batch_table_with_overrides(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"], reader="raw_text", writer="bin", byte_order="big")

    config = load_config(tmp_path)
    entry = config.batch_tables[0]
    assert entry["reader"] == "raw_text"
    assert entry["writer"] == "bin"
    assert entry["byte_order"] == "big"


def test_create_batch_table_preserves_existing_defaults(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "hex"\n')

    create_batch_table(tmp_path, "rows", ["ROW1.txt"])

    config = load_config(tmp_path)
    assert config.defaults.writer == "hex"
    assert len(config.batch_tables) == 1


def test_create_batch_table_duplicate_name_raises(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])
    with pytest.raises(BatchTableError):
        create_batch_table(tmp_path, "rows", ["ROW2.txt"])


def test_add_batch_table_source_appends(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])

    add_batch_table_source(tmp_path, "rows", "ROW2.txt")

    config = load_config(tmp_path)
    assert config.batch_tables[0]["sources"] == ["ROW1.txt", "ROW2.txt"]


def test_add_batch_table_source_idempotent(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])

    add_batch_table_source(tmp_path, "rows", "ROW1.txt")

    config = load_config(tmp_path)
    assert config.batch_tables[0]["sources"] == ["ROW1.txt"]


def test_add_batch_table_source_unknown_name_raises(tmp_path):
    with pytest.raises(BatchTableError):
        add_batch_table_source(tmp_path, "does_not_exist", "ROW1.txt")


def test_remove_batch_table_source_removes_literal_entry(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt", "ROW2.txt"])

    changed = remove_batch_table_source(tmp_path, "rows", "ROW1.txt")

    assert changed is True
    config = load_config(tmp_path)
    assert config.batch_tables[0]["sources"] == ["ROW2.txt"]


def test_remove_batch_table_source_no_file_returns_false(tmp_path):
    assert remove_batch_table_source(tmp_path, "rows", "ROW1.txt") is False


def test_remove_batch_table_source_unknown_name_returns_false(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])
    assert remove_batch_table_source(tmp_path, "does_not_exist", "ROW1.txt") is False


def test_remove_batch_table_source_unknown_source_returns_false(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])
    assert remove_batch_table_source(tmp_path, "rows", "ROW9.txt") is False


def test_remove_batch_table_entry_removes_whole_block(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])

    changed = remove_batch_table_entry(tmp_path, "rows")

    assert changed is True
    config = load_config(tmp_path)
    assert config.batch_tables == []


def test_remove_batch_table_entry_no_file_returns_false(tmp_path):
    assert remove_batch_table_entry(tmp_path, "rows") is False


def test_remove_batch_table_entry_unknown_name_returns_false(tmp_path):
    create_batch_table(tmp_path, "rows", ["ROW1.txt"])
    assert remove_batch_table_entry(tmp_path, "does_not_exist") is False


# --- [[cluster]] structural validation ---------------------------------

def test_cluster_parsed_into_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nwriter = "hex"\n'
    )
    config = load_config(tmp_path)
    assert config.clusters == [{"name": "sensors", "defaults": {"writer": "hex"}}]


def test_cluster_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('cluster = "not a list"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_entry_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('cluster = ["not a table"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_missing_name_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[cluster]]\ndefaults = { writer = "hex" }\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[cluster]]\nname = "sensors"\nbogus = "x"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_defaults_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[cluster]]\nname = "sensors"\ndefaults = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_defaults_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nbogus = "x"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_defaults_invalid_byte_order_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nbyte_order = "middle"\n'
    )
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_plugin_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[cluster]]\nname = "sensors"\nplugin = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_cluster_plugin_section_unvalidated(tmp_path):
    """[cluster.plugin.*] is deliberately NOT validated — plugin
    territory, same as the top-level [plugin.*]."""
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.plugin.c_source]\nanything = "goes"\n'
    )
    config = load_config(tmp_path)
    assert config.clusters[0]["plugin"] == {"c_source": {"anything": "goes"}}


# --- [[table_meta]] structural validation -------------------------------

def test_table_meta_parsed_into_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[table_meta]]\nname = "t1"\ncluster = "sensors"\ntags = ["a", "b"]\n'
    )
    config = load_config(tmp_path)
    assert config.table_meta == [{"name": "t1", "cluster": "sensors", "tags": ["a", "b"]}]


def test_table_meta_not_a_list_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('table_meta = "not a list"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_table_meta_entry_not_a_table_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('table_meta = ["not a table"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_table_meta_missing_name_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[table_meta]]\ntags = ["a"]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_table_meta_unknown_field_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[table_meta]]\nname = "t1"\nbogus = "x"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_table_meta_cluster_wrong_type_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[table_meta]]\nname = "t1"\ncluster = 123\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_table_meta_tags_not_a_list_of_strings_raises(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[table_meta]]\nname = "t1"\ntags = [1, 2]\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_no_cluster_or_table_meta_defaults_to_empty_list(tmp_path):
    config = load_config(tmp_path)
    assert config.clusters == []
    assert config.table_meta == []


# --- cluster resolution in resolve_config_with_provenance ---------------

def test_resolve_config_cluster_sits_between_global_and_sidecar(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "global_writer"\n\n'
        '[[cluster]]\nname = "sensors"\n\n'
        '[cluster.defaults]\nwriter = "cluster_writer"\n\n'
        '[[table_meta]]\nname = "t1"\ncluster = "sensors"\n'
    )
    src = tmp_path / "t1.raw"
    src.write_text("x")

    config, provenance = resolve_config_with_provenance(tmp_path, source_path=src)

    assert config.defaults.writer == "cluster_writer"
    assert provenance["defaults.writer"] == "cluster (sensors)"


def test_resolve_config_sidecar_wins_over_cluster(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nwriter = "cluster_writer"\n\n'
        '[[table_meta]]\nname = "t1"\ncluster = "sensors"\n'
    )
    src = tmp_path / "t1.raw"
    src.write_text("x")
    (tmp_path / "t1.config.toml").write_text('[defaults]\nwriter = "sidecar_writer"\n')

    config, provenance = resolve_config_with_provenance(tmp_path, source_path=src)

    assert config.defaults.writer == "sidecar_writer"
    assert provenance["defaults.writer"] == "sidecar (t1.config.toml)"


def test_resolve_config_unclustered_table_unaffected(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[defaults]\nwriter = "global_writer"\n\n'
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nwriter = "cluster_writer"\n'
    )
    src = tmp_path / "t2.raw"
    src.write_text("x")

    config = load_config(tmp_path, source_path=src)

    assert config.defaults.writer == "global_writer"


def test_resolve_config_table_name_explicit_overrides_source_path_stem(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nwriter = "cluster_writer"\n\n'
        '[[table_meta]]\nname = "explicit_name"\ncluster = "sensors"\n'
    )
    src = tmp_path / "different_stem.raw"
    src.write_text("x")

    config = load_config(tmp_path, source_path=src, table_name="explicit_name")

    assert config.defaults.writer == "cluster_writer"


def test_resolve_config_no_lookup_without_table_name_or_source_path(tmp_path):
    """Bare load_config(root) (no source_path/table_name — e.g. before
    a batch table's own cluster lookup is applied via effective_config)
    does no cluster resolution at all, and therefore doesn't even
    validate [[cluster]]/[[table_meta]] duplicates — this mirrors
    [[batch_table]] not being deep-validated by every load_config
    call either."""
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "dup"\n\n[[cluster]]\nname = "dup"\n'
    )
    config = load_config(tmp_path)
    assert config.defaults.writer is None


def test_resolve_config_duplicate_cluster_raises_when_table_name_given(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[cluster]]\nname = "dup"\n\n[[cluster]]\nname = "dup"\n'
    )
    with pytest.raises(ClusterError):
        load_config(tmp_path, table_name="t1")


def test_resolve_config_dangling_cluster_reference_raises_when_table_name_given(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[table_meta]]\nname = "t1"\ncluster = "does_not_exist"\n'
    )
    with pytest.raises(ClusterError):
        load_config(tmp_path, table_name="t1")


def test_resolve_config_duplicate_table_meta_raises_when_table_name_given(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[[table_meta]]\nname = "t1"\n\n[[table_meta]]\nname = "t1"\n'
    )
    with pytest.raises(TableMetaError):
        load_config(tmp_path, table_name="t1")


def test_resolve_config_table_with_no_meta_entry_unaffected(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[[cluster]]\nname = "sensors"\n')
    src = tmp_path / "t1.raw"
    src.write_text("x")
    config = load_config(tmp_path, source_path=src)
    assert config.defaults.writer is None
