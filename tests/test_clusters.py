from pathlib import Path

import pytest

from payload.core.clusters import Cluster, cluster_override_raw, resolve_clusters
from payload.core.config import load_config
from payload.core.errors import ClusterError


def _config_with(tmp_path: Path, toml_text: str):
    (tmp_path / "table-tool.toml").write_text(toml_text)
    return load_config(tmp_path)


def test_resolve_clusters_empty_by_default(tmp_path):
    config = _config_with(tmp_path, "[defaults]\n")
    assert resolve_clusters(tmp_path, config) == {}


def test_resolve_clusters_parses_defaults_and_plugin(tmp_path):
    config = _config_with(
        tmp_path,
        '[[cluster]]\nname = "sensors"\n\n'
        '[cluster.defaults]\nwriter = "hex"\n\n'
        '[cluster.plugin.c_source]\ncompiler = "arm-gcc"\n',
    )

    clusters = resolve_clusters(tmp_path, config)

    assert set(clusters) == {"sensors"}
    c = clusters["sensors"]
    assert c.name == "sensors"
    assert c.defaults == {"writer": "hex"}
    assert c.plugin == {"c_source": {"compiler": "arm-gcc"}}


def test_resolve_clusters_entry_with_no_overrides(tmp_path):
    config = _config_with(tmp_path, '[[cluster]]\nname = "empty"\n')
    clusters = resolve_clusters(tmp_path, config)
    assert clusters["empty"].defaults == {}
    assert clusters["empty"].plugin == {}


def test_resolve_clusters_duplicate_name_raises(tmp_path):
    config = _config_with(
        tmp_path,
        '[[cluster]]\nname = "sensors"\n\n[[cluster]]\nname = "sensors"\n',
    )
    with pytest.raises(ClusterError):
        resolve_clusters(tmp_path, config)


def test_cluster_override_raw_none_is_empty():
    assert cluster_override_raw(None) == {}


def test_cluster_override_raw_empty_cluster_is_empty():
    assert cluster_override_raw(Cluster(name="x")) == {}


def test_cluster_override_raw_defaults_only():
    c = Cluster(name="x", defaults={"writer": "hex"})
    assert cluster_override_raw(c) == {"defaults": {"writer": "hex"}}


def test_cluster_override_raw_plugin_only():
    c = Cluster(name="x", plugin={"c_source": {"compiler": "gcc"}})
    assert cluster_override_raw(c) == {"plugin": {"c_source": {"compiler": "gcc"}}}


def test_cluster_override_raw_both():
    c = Cluster(name="x", defaults={"writer": "hex"}, plugin={"c_source": {"compiler": "gcc"}})
    assert cluster_override_raw(c) == {
        "defaults": {"writer": "hex"},
        "plugin": {"c_source": {"compiler": "gcc"}},
    }
