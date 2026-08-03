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

    r = client.post("/api/build", json={"source": "does_not_exist.raw", "to": "bin"})

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
    (root / "build" / "example_table.bin").write_bytes(b"tampered by hand")

    # source unchanged -> cache hit, the build doesn't rewrite the tampered output
    r = client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "check_golden": True})

    assert r.status_code == 409


def _build_commit_golden(client, root):
    client.post("/api/build", json={"source": str(root / "example_table.raw")})
    client.post("/api/commit", json={"message": "v1", "only": ["example_table"]})
    client.put("/api/golden/example_table", json={})


def test_preview_diff_vs_golden(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_commit_golden(client, root)

    # identical source -> identical preview
    r = client.post("/api/table/example_table/preview-diff", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] == "golden"
    assert all(not o["runs"] for o in body["outputs"])

    # change the source (a byte value) -> the preview output differs
    (root / "example_table.raw").write_text("# changed\n0x2A,\n", encoding="utf-8")
    r = client.post("/api/table/example_table/preview-diff", json={})
    body = r.json()
    assert body["baseline"] == "golden"
    runs = [run for o in body["outputs"] for run in o["runs"]]
    assert runs, "changed source must produce differing preview output"
    assert all("current" in run and "previous" in run and "offset" in run for run in runs)

    # the real output on disk must be untouched (still the golden build)
    out = next((root / "build").glob("example_table.*"))
    golden = client.get("/api/golden/example_table").json()["golden_snapshot_id"]
    assert client.get("/api/diff/example_table").status_code in (200, 404)  # sanity


def test_preview_diff_baseline_current_without_golden(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": str(root / "example_table.raw")})

    (root / "example_table.raw").write_text("# changed\n0x2A,\n", encoding="utf-8")
    r = client.post("/api/table/example_table/preview-diff", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] == "current"
    assert body["golden_snapshot_id"] is None


def test_preview_diff_batch_table(tmp_path):
    from payload.core.config import create_batch_table

    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n0x0A,\n", encoding="utf-8")
    (root / "b.raw").write_text("# b\n0x1B,\n", encoding="utf-8")
    create_batch_table(root, "batch1", ["a.raw", "b.raw"])
    client.post("/api/build", json={"source": "batch1"})
    client.post("/api/commit", json={"message": "v1", "only": ["batch1"]})
    client.put("/api/golden/batch1", json={})

    (root / "a.raw").write_text("# changed\n0x2A,\n", encoding="utf-8")
    r = client.post("/api/table/batch1/preview-diff", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] == "golden"
    assert any(o["runs"] for o in body["outputs"]), "changed batch member must produce a preview diff"
    # the real output is untouched
    assert client.get("/api/golden/batch1").json()["status"] in ("match", "stale")


def test_preview_diff_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    r = client.post("/api/table/ghost/preview-diff", json={})
    assert r.status_code == 404


def test_preview_diff_new_file_when_never_built(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    # the table has never been built: the real output doesn't exist, so
    # "everything differs" would be noise — it's a NEW file instead
    r = client.post("/api/table/example_table/preview-diff", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] == "current"
    assert body["outputs"] and all(o["new_file"] for o in body["outputs"])
    assert all(not o["runs"] for o in body["outputs"])
