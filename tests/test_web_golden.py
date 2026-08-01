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


def _build_and_commit(client: TestClient, message: str = "v1") -> int:
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": message})
    log = client.get("/api/log/example_table").json()
    return log["snapshots"][-1]["id"]


def test_golden_get_missing_when_never_set(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_and_commit(client)

    r = client.get("/api/golden/example_table")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "missing"
    assert body["golden_snapshot_id"] is None


def test_golden_get_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/golden/non_esiste")

    assert r.status_code == 404


def test_golden_set_defaults_to_latest_snapshot(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)

    r = client.put("/api/golden/example_table", json={})

    assert r.status_code == 200
    assert r.json()["golden_snapshot_id"] == snap_id


def test_golden_set_explicit_snapshot(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap1 = _build_and_commit(client, "v1")
    (root / "example_table.raw").write_text("0x99\n")
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "force": True})
    client.post("/api/commit", json={"message": "v2"})

    r = client.put("/api/golden/example_table", json={"snapshot_id": snap1})

    assert r.status_code == 200
    assert r.json()["golden_snapshot_id"] == snap1


def test_golden_set_no_snapshot_yet_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/golden/example_table", json={})

    assert r.status_code == 404


def test_golden_set_unknown_snapshot_id_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_and_commit(client)

    r = client.put("/api/golden/example_table", json={"snapshot_id": 999})

    assert r.status_code == 404


def test_golden_get_match_after_set(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})

    r = client.get("/api/golden/example_table")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "match"
    assert body["golden_snapshot_id"] == snap_id


def test_golden_get_stale_when_source_changed(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})
    (root / "example_table.raw").write_text("0x99\n")

    r = client.get("/api/golden/example_table")

    assert r.json()["status"] == "stale"


def test_golden_get_mismatch_on_tampered_output(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})
    (root / "build" / "example_table.bin").write_bytes(b"manomesso")

    r = client.get("/api/golden/example_table")

    assert r.json()["status"] == "mismatch"


def test_golden_clear(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})

    r1 = client.delete("/api/golden/example_table")
    r2 = client.delete("/api/golden/example_table")

    assert r1.status_code == 200
    assert r1.json()["status"] == "cleared"
    assert r2.json()["status"] == "not_set"  # idempotente

    after = client.get("/api/golden/example_table")
    assert after.json()["status"] == "missing"


def test_golden_diff_empty_when_matching(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})

    r = client.get("/api/golden/example_table/diff")

    assert r.status_code == 200
    assert r.json()["diffs"] == {}


def test_golden_diff_shows_chunks_for_changed_output(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    snap_id = _build_and_commit(client)
    client.put("/api/golden/example_table", json={"snapshot_id": snap_id})
    (root / "build" / "example_table.bin").write_bytes(b"\xff\xff\xff\xff")

    r = client.get("/api/golden/example_table/diff")

    assert r.status_code == 200
    diffs = r.json()["diffs"]
    assert "example_table.bin" in diffs
    assert len(diffs["example_table.bin"]) > 0


def test_golden_diff_missing_golden_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_and_commit(client)

    r = client.get("/api/golden/example_table/diff")

    assert r.status_code == 404
