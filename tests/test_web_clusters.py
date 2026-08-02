"""Web tests for /api/clusters and /api/table/{name}/cluster|tags —
web counterpart of 'pld cluster'/'pld tag' in cli.py, same pattern
(TestClient) as test_web_plugins.py."""
from pathlib import Path

from starlette.testclient import TestClient
from typer.testing import CliRunner

from payload.cli import app as cli_app
from payload.web.app import create_app

runner = CliRunner()


def _init_project(tmp_path: Path, name: str = "proj") -> Path:
    result = runner.invoke(cli_app, ["init", str(tmp_path / name)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root))


# --- GET/POST /api/clusters ---------------------------------------------------

def test_clusters_list_empty(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/clusters")

    assert r.status_code == 200
    body = r.json()
    assert body["clusters"] == []
    assert "defaults" in body["schema"]


def test_cluster_create_and_list(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/clusters", json={"name": "sensors", "defaults": {"writer": "hex"}})

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "sensors"
    assert body["defaults"] == {"writer": "hex"}
    assert body["member_count"] == 0
    assert body["members"] == []

    r2 = client.get("/api/clusters")
    assert [c["name"] for c in r2.json()["clusters"]] == ["sensors"]


def test_cluster_create_missing_name_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/clusters", json={})

    assert r.status_code == 400


def test_cluster_create_duplicate_name_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})

    r = client.post("/api/clusters", json={"name": "sensors"})

    assert r.status_code == 400
    assert r.json()["error"] == "ClusterError"


def test_cluster_create_with_plugin_override(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/clusters", json={"name": "sensors", "plugin": {"c_source": {"compiler": "gcc"}}})

    assert r.status_code == 200
    assert r.json()["plugin"] == {"c_source": {"compiler": "gcc"}}


# --- PUT /api/clusters/{name} --------------------------------------------------

def test_cluster_update_replaces_defaults(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors", "defaults": {"writer": "hex"}})

    r = client.put("/api/clusters/sensors", json={"defaults": {"writer": "bin"}})

    assert r.status_code == 200
    assert r.json()["defaults"] == {"writer": "bin"}


def test_cluster_update_omitted_field_untouched(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors", "defaults": {"writer": "hex"}})

    r = client.put("/api/clusters/sensors", json={})

    assert r.status_code == 200
    assert r.json()["defaults"] == {"writer": "hex"}


def test_cluster_update_unknown_name_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/clusters/does_not_exist", json={"defaults": {"writer": "hex"}})

    assert r.status_code == 400


# --- DELETE /api/clusters/{name} -----------------------------------------------

def test_cluster_delete(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})

    r = client.delete("/api/clusters/sensors")

    assert r.status_code == 200
    assert r.json()["status"] == "deleted"


def test_cluster_delete_not_found(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.delete("/api/clusters/does_not_exist")

    assert r.status_code == 200
    assert r.json()["status"] == "not_found"


def test_cluster_delete_with_members_refuses_without_force(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})

    r = client.delete("/api/clusters/sensors")

    assert r.status_code == 400
    assert r.json()["error"] == "ClusterError"


def test_cluster_delete_with_force_succeeds(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})

    r = client.delete("/api/clusters/sensors?force=true")

    assert r.status_code == 200
    assert r.json()["status"] == "deleted"


# --- PUT /api/table/{table_name}/cluster ---------------------------------------

def test_table_cluster_assign(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})

    r = client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})

    assert r.status_code == 200
    assert r.json() == {"table_name": "example_table", "cluster": "sensors"}


def test_table_cluster_clear(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})

    r = client.put("/api/table/example_table/cluster", json={"cluster": None})

    assert r.status_code == 200
    assert r.json()["cluster"] is None


def test_table_cluster_missing_field_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/cluster", json={})

    assert r.status_code == 400


def test_table_cluster_wrong_type_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/cluster", json={"cluster": 123})

    assert r.status_code == 400


def test_table_cluster_unknown_cluster_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/cluster", json={"cluster": "does_not_exist"})

    assert r.status_code == 400


# --- GET/PUT /api/table/{table_name}/tags --------------------------------------

def test_table_tags_get_empty(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/table/example_table/tags")

    assert r.status_code == 200
    assert r.json() == {"tags": []}


def test_table_tags_put_and_get(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/tags", json={"tags": ["prod", "beta"]})
    assert r.status_code == 200
    assert r.json() == {"table_name": "example_table", "tags": ["prod", "beta"]}

    r2 = client.get("/api/table/example_table/tags")
    assert r2.json() == {"tags": ["prod", "beta"]}


def test_table_tags_put_not_a_list_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/tags", json={"tags": "prod"})

    assert r.status_code == 400


def test_table_tags_put_not_a_list_of_strings_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/table/example_table/tags", json={"tags": [1, 2]})

    assert r.status_code == 400


# --- /api/report and /api/status expose cluster/tags ---------------------------

def test_report_includes_cluster_and_tags(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})
    client.put("/api/table/example_table/tags", json={"tags": ["prod"]})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["cluster"] == "sensors"
    assert row["tags"] == ["prod"]


def test_report_cluster_defaults_override_applied(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors", "defaults": {"byte_order": "big"}})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["byte_order"] == "big"


def test_status_includes_cluster_and_tags(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/clusters", json={"name": "sensors"})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})
    client.put("/api/table/example_table/tags", json={"tags": ["prod"]})

    r = client.get("/api/status")

    row = r.json()["tables"][0]
    assert row["cluster"] == "sensors"
    assert row["tags"] == ["prod"]


def test_table_delete_cleans_up_table_meta_entry(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/table/example_table/tags", json={"tags": ["prod"]})

    client.post("/api/table/delete", json={"table_name": "example_table", "confirm": True})

    r = client.get("/api/table/example_table/tags")
    assert r.json() == {"tags": []}


def test_meta_get_put_roundtrip(tmp_path):
    from payload.core.config import set_table_meta_fields

    root = _init_project(tmp_path)
    client = _client(root)
    set_table_meta_fields(root, "example_table", notes="n", properties={"address": "0x8000"})

    got = client.get("/api/table/example_table/meta").json()
    assert got["notes"] == "n"
    assert got["properties"] == {"address": "0x8000"}
    assert got["tags"] == []

    r = client.put("/api/table/example_table/meta", json={"notes": "updated", "properties": {"version": "3"}})
    assert r.status_code == 200
    got = client.get("/api/table/example_table/meta").json()
    assert got["notes"] == "updated"
    assert got["properties"] == {"version": "3"}

    # invalid payloads are rejected
    assert client.put("/api/table/example_table/meta", json={"notes": 42}).status_code == 400
    assert client.put("/api/table/example_table/meta", json={"properties": {"a": 1}}).status_code == 400
