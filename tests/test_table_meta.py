from pathlib import Path

import pytest

from payload.core.clusters import resolve_clusters
from payload.core.config import load_config
from payload.core.errors import ClusterError, TableMetaError
from payload.core.table_meta import TableMeta, resolve_table_meta


def _config_with(tmp_path: Path, toml_text: str):
    (tmp_path / "table-tool.toml").write_text(toml_text)
    return load_config(tmp_path)


def test_resolve_table_meta_empty_by_default(tmp_path):
    config = _config_with(tmp_path, "[defaults]\n")
    assert resolve_table_meta(tmp_path, config, {}) == {}


def test_resolve_table_meta_parses_cluster_and_tags(tmp_path):
    config = _config_with(
        tmp_path,
        '[[cluster]]\nname = "sensors"\n\n'
        '[[table_meta]]\nname = "t1"\ncluster = "sensors"\ntags = ["a", "b"]\n',
    )
    clusters = resolve_clusters(tmp_path, config)

    metas = resolve_table_meta(tmp_path, config, clusters)

    assert set(metas) == {"t1"}
    m = metas["t1"]
    assert m.name == "t1"
    assert m.cluster == "sensors"
    assert m.tags == ["a", "b"]


def test_resolve_table_meta_entry_with_no_cluster_or_tags(tmp_path):
    config = _config_with(tmp_path, '[[table_meta]]\nname = "t1"\n')
    metas = resolve_table_meta(tmp_path, config, {})
    assert metas["t1"] == TableMeta(name="t1", cluster=None, tags=[])


def test_resolve_table_meta_duplicate_name_raises(tmp_path):
    config = _config_with(
        tmp_path,
        '[[table_meta]]\nname = "t1"\n\n[[table_meta]]\nname = "t1"\n',
    )
    with pytest.raises(TableMetaError):
        resolve_table_meta(tmp_path, config, {})


def test_resolve_table_meta_dangling_cluster_reference_raises(tmp_path):
    config = _config_with(tmp_path, '[[table_meta]]\nname = "t1"\ncluster = "does_not_exist"\n')
    with pytest.raises(ClusterError):
        resolve_table_meta(tmp_path, config, {})


def test_resolve_table_meta_skips_cluster_cross_check_when_clusters_is_none(tmp_path):
    """clusters=None means the caller deliberately skipped the
    cross-check — a dangling reference must NOT raise in that case."""
    config = _config_with(tmp_path, '[[table_meta]]\nname = "t1"\ncluster = "does_not_exist"\n')
    metas = resolve_table_meta(tmp_path, config, None)
    assert metas["t1"].cluster == "does_not_exist"
