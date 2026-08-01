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


def test_build_requires_source(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={})

    assert r.status_code == 400


def test_build_produces_output(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    assert r.status_code == 200
    body = r.json()
    assert body["was_built"] is True
    assert body["outputs"][0].endswith("example_table.bin")
    assert (root / "build" / "example_table.bin").exists()


def test_build_second_call_served_from_cache(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    assert r.json()["was_built"] is False


def test_build_dry_run_does_not_write(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "dry_run": True})

    assert r.status_code == 200
    assert not (root / "build" / "example_table.bin").exists()


def test_build_unknown_source_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={"source": "non_esiste.raw", "to": "bin"})

    assert r.status_code == 404


def test_build_check_golden_missing_is_not_an_error(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "check_golden": True})

    assert r.status_code == 200
    assert r.json()["golden_status"] == "missing"


def test_build_check_golden_match(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "v1"})
    log = client.get("/api/log/example_table").json()
    snapshot_id = log["snapshots"][0]["id"]
    client.put("/api/golden/example_table", json={"snapshot_id": snapshot_id})

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "check_golden": True})

    assert r.status_code == 200
    assert r.json()["golden_status"] == "match"


def test_build_check_golden_stale_when_source_changed_409(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "v1", "only": ["example_table"]})
    log = client.get("/api/log/example_table").json()
    client.put("/api/golden/example_table", json={"snapshot_id": log["snapshots"][0]["id"]})
    (root / "example_table.raw").write_text("0x99\n")

    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "force": True, "check_golden": True})

    assert r.status_code == 409


def test_build_check_golden_mismatch_on_tampered_output_409(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "v1"})
    log = client.get("/api/log/example_table").json()
    client.put("/api/golden/example_table", json={"snapshot_id": log["snapshots"][0]["id"]})
    (root / "build" / "example_table.bin").write_bytes(b"manomesso a mano")

    # sorgente invariato -> cache hit, il build non riscrive l'output manomesso
    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "check_golden": True})

    assert r.status_code == 409
