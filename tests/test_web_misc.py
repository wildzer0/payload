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

    scaffold_local_plugin("new_check", "doctor-check", root / "plugins")
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


def test_table_analyze_output(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.get("/api/table/example_table/analyze")

    assert r.status_code == 200
    body = r.json()
    assert "entropy" in body
    assert "freq" in body
    assert "magic" in body
    assert body["path"].endswith("example_table.bin")


def test_table_analyze_without_output(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    r = client.get("/api/table/example_table/analyze")
    assert r.status_code == 404  # NoBuildOutputError


def test_table_analyze_unknown_table(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    assert client.get("/api/table/ghost/analyze").status_code == 404


def test_view_slices_with_offset_and_limit(tmp_path):
    import base64

    root = _init_project(tmp_path)
    client = _client(root)
    # a 1024-byte source in raw_text format (0x00..0xFF repeated 4x)
    blob = bytes(range(256)) * 4
    (root / "blob.raw").write_text(
        "\n".join(f"0x{b:02X}," for b in blob), encoding="utf-8"
    )
    src = str(root / "blob.raw")

    first = client.get("/api/view", params={"source": src, "offset": 0, "limit": 256}).json()
    assert first["offset"] == 0
    assert first["has_more"] is True
    # the page is the byte slice: page 1 ends with 0xFF
    assert base64.b64decode(first["data_base64"])[-1] == 0xFF

    second = client.get("/api/view", params={"source": src, "offset": 256, "limit": 256}).json()
    assert second["offset"] == 256
    assert second["has_more"] is True
    # page 2 starts right where page 1 ended (0x00)
    assert base64.b64decode(second["data_base64"])[0] == 0x00

    last = client.get("/api/view", params={"source": src, "offset": 1024, "limit": 256}).json()
    assert last["offset"] == 1024
    assert last["has_more"] is False
    assert base64.b64decode(last["data_base64"]) == b""


def test_view_slices_keep_comments_absolute(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/view", params={"source": str(root / "example_table.raw"), "offset": 0, "limit": 64}).json()
    # comments in the slice keep their absolute (file) offsets
    assert all(0 <= c["offset"] < 64 for c in r["comments"])


def test_view_tolerates_malformed_paging_params(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    # non-numeric offset/limit must fall back to defaults, not 500
    r = client.get("/api/view", params={"source": str(root / "example_table.raw"), "offset": "abc", "limit": "-3"})
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 0
    assert body["limit"] == 0  # 0 = whole file
    assert body["has_more"] is False


def test_report_html_endpoint(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    from payload.core.config import set_table_meta_fields

    set_table_meta_fields(root, "example_table", notes="calibrated", properties={"address": "0x8000"})

    r = client.get("/api/report/html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "<title>Table report" in body
    assert "example_table" in body
    assert "calibrated" in body  # notes
    assert "0x8000" in body  # properties


def test_report_collect_matches_json_endpoint(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    json_rows = client.get("/api/report").json()["tables"]
    from payload.core.report import collect_report_data

    core_rows = collect_report_data(root)["tables"]
    assert {t["name"] for t in json_rows} == {t["name"] for t in core_rows}


def test_report_html_empty_project(tmp_path):
    root = _init_project(tmp_path)
    (root / "example_table.raw").unlink()
    client = _client(root)
    body = client.get("/api/report/html").text
    assert "No table in this project" in body


def test_report_html_golden_statuses(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    source = root / "example_table.raw"
    original = source.read_text()

    # build + commit + set golden -> match ("built")
    client.post("/api/build", json={"source": str(source)})
    client.post("/api/commit", json={"message": "v1", "only": ["example_table"]})
    client.put("/api/golden/example_table", json={})
    assert "built" in client.get("/api/report/html").text

    # source changed -> stale
    source.write_text("# changed\n", encoding="utf-8")
    client.post("/api/build", json={"source": str(source)})
    assert "stale" in client.get("/api/report/html").text

    # source EXACTLY back to the golden, but output tampered -> mismatch
    source.write_text(original, encoding="utf-8")
    client.post("/api/build", json={"source": str(source)})
    out = next((root / "build").glob("example_table.*"))
    out.write_bytes(out.read_bytes() + b"tampered")
    assert "golden mismatch" in client.get("/api/report/html").text
