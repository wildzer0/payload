import io
import zipfile
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


def test_view_requires_source(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/view")

    assert r.status_code == 400


def test_view_returns_bytes_and_comments(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/view", params={"source": str(root / "example_table.raw")})

    assert r.status_code == 200
    body = r.json()
    assert body["length"] > 0
    assert "data_base64" in body


def test_report_table_never_built(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/report")

    assert r.status_code == 200
    row = r.json()["tables"][0]
    assert row["output_size"] is None
    assert row["last_snapshot"] is None


def test_report_table_built_with_golden_and_snapshot(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "first"})
    client.put("/api/golden/example_table", json={})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["golden_status"] == "match"
    assert row["golden_snapshot_id"] == 1
    assert row["last_snapshot"]["id"] == 1


def test_report_resolves_reader_and_writer_by_default(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["resolved_reader"] == "raw_text"
    assert row["resolved_writer"] == "bin"
    assert row["reader_override"] is None
    # 'pld init' already writes "writer = bin" in the global table-tool.toml
    assert row["writer_override"] == "bin"
    assert row["has_sidecar"] is False
    assert row["pipeline_explicit"] is False
    assert "source_mtime" in row


def test_report_reflects_sidecar_overrides(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"writer": "hex"}})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["writer_override"] == "hex"
    assert row["resolved_writer"] == "hex"
    assert row["has_sidecar"] is True


def test_report_hides_resolved_reader_writer_when_pipeline_explicit(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "bin"}]
    client.put("/api/pipeline/example_table", json={"stages": stages})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["pipeline_explicit"] is True
    assert row["resolved_reader"] is None
    assert row["resolved_writer"] is None


def test_report_degrades_gracefully_when_reader_unresolvable(tmp_path):
    """A nonexistent reader override (typo, or a plugin removed after
    it was configured) must not break the whole dashboard: the row
    stays, simply without a resolved reader/writer."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"reader": "reader_that_does_not_exist"}})

    r = client.get("/api/report")

    row = r.json()["tables"][0]
    assert row["reader_override"] == "reader_that_does_not_exist"
    assert row["resolved_reader"] is None
    assert row["resolved_writer"] is None


def test_download_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/table/does_not_exist/download")

    assert r.status_code == 404


def test_download_no_output_yet_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/table/example_table/download")

    assert r.status_code == 404
    assert r.json()["error"] == "NoBuildOutputError"


def test_download_single_output_serves_file_directly(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.get("/api/table/example_table/download")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert 'filename="example_table.bin"' in r.headers["content-disposition"]
    assert r.content == (root / "build" / "example_table.bin").read_bytes()


def test_download_fan_out_zips_all_outputs(tmp_path):
    root = _init_project(tmp_path)
    (root / "table-tool.toml").write_text(
        (root / "table-tool.toml").read_text()
        + '\n[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }, '
        '{ type = "writer", name = "hex" },'
        ']\n'
    )
    client = _client(root)
    build_r = client.post("/api/build", json={"source": "example_table.raw"})
    assert build_r.status_code == 200, build_r.text

    r = client.get("/api/table/example_table/download")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="example_table-output.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert set(zf.namelist()) == {"example_table.bin", "example_table.hex"}


def test_doctor_runs_checks(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/doctor")

    assert r.status_code == 200
    checks = r.json()["checks"]
    assert any(c["name"] == "git" for c in checks)


def test_doctor_survives_unimplemented_local_doctor_check(tmp_path):
    """Regression found by a user: opening the Doctor page with a
    local DOCTOR_CHECK plugin created but never implemented (scaffold
    with 'raise NotImplementedError') used to throw an exception in
    the whole route instead of only flagging THAT check as failed."""
    root = _init_project(tmp_path)
    from payload.plugin_scaffold import scaffold_local_plugin

    scaffold_local_plugin("new_check", "doctor-check", root / "local_plugins")
    client = _client(root)

    r = client.get("/api/doctor")

    assert r.status_code == 200
    checks = r.json()["checks"]
    crashed = next(c for c in checks if c["name"] == "new_check")
    assert crashed["status"] == "fail"
    assert "NotImplementedError" in crashed["message"]
    # and the other checks must still be present
    assert any(c["name"] == "git" for c in checks)


def test_export_produces_zip(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/export")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert any("example_table.raw" in n for n in zf.namelist())


def test_clean_noop_when_nothing_to_clean(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/clean", json={"target": "cache"})

    assert r.status_code == 200
    assert r.json()["status"] == "noop"


def test_clean_requires_confirmation(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.post("/api/clean", json={"target": "build"})

    assert r.status_code == 200
    assert r.json()["status"] == "confirmation_required"
    assert (root / "build").exists()


def test_clean_confirmed_removes_directory(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.post("/api/clean", json={"target": "build", "confirm": True})

    assert r.status_code == 200
    assert r.json()["status"] == "cleaned"
    assert not (root / "build").exists()


def test_clean_unknown_target_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/clean", json={"target": "boh"})

    assert r.status_code == 400


def test_clean_golden_target_clears_pointers_not_a_directory(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "v1"})
    client.put("/api/golden/example_table", json={})

    r = client.post("/api/clean", json={"target": "golden", "confirm": True})

    assert r.status_code == 200
    assert r.json()["status"] == "cleaned"
    assert r.json()["golden_tables"] == ["example_table"]
    after = client.get("/api/golden/example_table")
    assert after.json()["status"] == "missing"
