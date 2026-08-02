"""Table rename (with history migration) and clone — core + web.
A rename is end to end: source file, sidecar, history (manifest +
golden/head pointers) and the name in table-tool.toml; a clone copies
source + sidecar + tags/cluster and starts with fresh history."""
from pathlib import Path

from starlette.testclient import TestClient
from typer.testing import CliRunner

from payload.cli import app as cli_app
from payload.core.history import HistoryStore
from payload.web.app import create_app

runner = CliRunner()


def _init_project(tmp_path: Path, name: str = "proj") -> Path:
    result = runner.invoke(cli_app, ["init", str(tmp_path / name)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root), raise_server_exceptions=False)


def _build_and_commit(client: TestClient) -> None:
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "v1"})


def _prepare(tmp_path, name="proj"):
    """A project with a committed table, a sidecar and tags."""
    root = _init_project(tmp_path, name)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"writer": "bin"}})
    client.put("/api/table/example_table/tags", json={"tags": ["alpha", "beta"]})
    _build_and_commit(client)
    client.put("/api/golden/example_table", json={})
    return root, client


# ---------- rename ----------

def test_rename_migrates_everything(tmp_path):
    root, client = _prepare(tmp_path)
    r = client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "renamed"})
    assert r.status_code == 200
    assert r.json()["to"] == "renamed"

    # source + sidecar moved on disk
    assert not (root / "example_table.raw").exists()
    assert (root / "renamed.raw").exists()
    assert not (root / "example_table.config.toml").exists()
    assert (root / "renamed.config.toml").exists()

    # history migrated: manifest, golden and head follow the new name
    history = HistoryStore(root)
    assert history.last_snapshot("renamed") is not None
    assert history.last_snapshot("example_table") is None
    assert history.golden_snapshot_id("renamed") is not None
    assert history.golden_snapshot_id("example_table") is None

    # tags followed ([[table_meta]] name rewritten in table-tool.toml)
    tags = client.get("/api/table/renamed/tags").json()
    assert tags["tags"] == ["alpha", "beta"]

    # the table is usable under the new name
    report = client.get("/api/report").json()
    assert "renamed" in {t["name"] for t in report["tables"]}


def test_rename_requires_new_name(tmp_path):
    root, client = _prepare(tmp_path)
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "example_table"}).status_code == 400


def test_rename_refuses_collision_and_invalid_names(tmp_path):
    root, client = _prepare(tmp_path)
    # collision with an existing table
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "example_table"}).status_code == 400
    # path separator / leading dot
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "a/b"}).status_code == 400
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": ".hidden"}).status_code == 400


def test_rename_unknown_table(tmp_path):
    root, client = _prepare(tmp_path)
    assert client.post("/api/table/rename", json={"table_name": "ghost", "new_name": "x"}).status_code == 404


# ---------- clone ----------

def test_clone_copies_source_sidecar_and_tags(tmp_path):
    root, client = _prepare(tmp_path)
    r = client.post("/api/table/clone", json={"table_name": "example_table", "new_name": "copy"})
    assert r.status_code == 200
    assert (root / "copy.raw").read_bytes() == (root / "example_table.raw").read_bytes()
    assert (root / "copy.config.toml").exists()
    # tags/cluster copied
    tags = client.get("/api/table/copy/tags").json()
    assert tags["tags"] == ["alpha", "beta"]
    # history is FRESH: the clone has nothing committed, the original keeps its own
    history = HistoryStore(root)
    assert history.last_snapshot("copy") is None
    assert history.last_snapshot("example_table") is not None


def test_clone_refuses_collision_and_batch(tmp_path):
    root, client = _prepare(tmp_path)
    assert client.post("/api/table/clone", json={"table_name": "example_table", "new_name": "example_table"}).status_code == 400
    assert client.post("/api/table/clone", json={"table_name": "ghost", "new_name": "x"}).status_code == 404


# ---------- edge branches (100% coverage) ----------

def test_rename_migrates_head_pointer(tmp_path):
    """After a restore the head.json pointer must follow the rename too."""
    root, client = _prepare(tmp_path)
    client.post("/api/restore", json={"table_name": "example_table", "confirm": True})
    client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "renamed"})
    history = HistoryStore(root)
    assert history.head_snapshot_id("renamed") is not None
    assert history.head_snapshot_id("example_table") is None


def test_rename_without_config_file(tmp_path):
    """A bare folder with a source and no table-tool.toml can still be
    renamed (the config rewrite has nothing to do, and must not fail)."""
    from payload.core.table_admin import rename_table
    root = tmp_path / "bare"
    root.mkdir()
    (root / "x.raw").write_text("hello")
    r = rename_table(root, "x", "y")
    assert r["to"] == "y"
    assert (root / "y.raw").exists()


def test_rename_to_an_existing_table(tmp_path):
    root, client = _prepare(tmp_path)
    (root / "other.raw").write_text("other")
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "other"}).status_code == 400


def test_rename_when_new_source_exists_on_disk(tmp_path):
    # a directory named "<name>.raw" is on disk but is NOT a discovered
    # table — this exercises the new_src.exists() guard specifically
    root, client = _prepare(tmp_path)
    (root / "renamed.raw").mkdir()
    assert client.post("/api/table/rename", json={"table_name": "example_table", "new_name": "renamed"}).status_code == 400


def test_clone_refuses_batch(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/table/import", data={"new_batch": "rows"}, files=[("file", ("ROW1.txt", b"r1", "text/plain"))])
    assert client.post("/api/table/clone", json={"table_name": "rows", "new_name": "rows2"}).status_code == 400


def test_clone_when_new_source_exists_on_disk(tmp_path):
    root, client = _prepare(tmp_path)
    (root / "copy.raw").mkdir()
    assert client.post("/api/table/clone", json={"table_name": "example_table", "new_name": "copy"}).status_code == 400


def test_clone_copies_cluster(tmp_path):
    root, client = _prepare(tmp_path)
    client.post("/api/clusters", json={"name": "sensors", "defaults": {}})
    client.put("/api/table/example_table/cluster", json={"cluster": "sensors"})
    client.post("/api/table/clone", json={"table_name": "example_table", "new_name": "copy"})
    meta = client.get("/api/table/copy/tags").json()  # tags route reflects the cluster too
    # verify the cluster was copied via the report/table_meta
    from payload.core.clusters import resolve_clusters
    from payload.core.config import load_config
    from payload.core.table_meta import resolve_table_meta
    metas = resolve_table_meta(root, load_config(root), resolve_clusters(root, load_config(root)))
    assert metas["copy"].cluster == "sensors"


def test_rename_clone_missing_params(tmp_path):
    root, client = _prepare(tmp_path)
    assert client.post("/api/table/rename", json={"table_name": "example_table"}).status_code == 400
    assert client.post("/api/table/clone", json={"table_name": "example_table"}).status_code == 400
    assert client.post("/api/table/rename", json={"new_name": "x"}).status_code == 400
