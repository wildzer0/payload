"""
Web tests for batch tables ([[batch_table]], see
src/payload/docs/BATCH.md) — same pattern (TestClient) as
test_web_build.py/test_web_config.py, covers the batch branches of
every route touched by the generalization (build, status, commit,
diff, restore, golden, report, export, sidecar/pipeline reject, source
editor reject)."""
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


def _add_batch_table(root: Path, name="rows", sources='["ROW*.txt"]', extra=""):
    (root / "table-tool.toml").write_text(
        (root / "table-tool.toml").read_text()
        + f'\n[[batch_table]]\nname = "{name}"\nsources = {sources}\n{extra}'
    )


def _write_rows(root: Path, n=2):
    for i in range(1, n + 1):
        (root / f"ROW{i}.txt").write_text(f"0x0{i}\n")


def test_build_batch_table_by_name(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    r = client.post("/api/build", json={"source": "rows"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["was_built"] is True
    assert body["outputs"][0].endswith("rows.bin")


def test_build_unknown_name_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/build", json={"source": "does_not_exist"})

    assert r.status_code == 404


def test_build_all_stream_includes_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    with client.stream("GET", "/api/build-all/stream") as r:
        body = "".join(r.iter_text())

    assert "rows" in body
    assert (root / "build" / "rows.bin").exists()
    assert not (root / "build" / "ROW1.bin").exists()


def test_status_shows_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    r = client.get("/api/status")

    assert r.status_code == 200
    rows_entry = next(t for t in r.json()["tables"] if t["name"] == "rows")
    assert rows_entry["is_batch"] is True
    assert rows_entry["source_count"] == 2
    assert rows_entry["path"] is None


def test_commit_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})

    r = client.post("/api/commit", json={"message": "v1"})

    assert r.status_code == 200, r.text
    committed = r.json()["committed"]
    assert any(c["name"] == "rows" for c in committed)


def test_diff_batch_table_reports_changed_member_file(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})

    (root / "ROW2.txt").write_text("0x99\n")

    r = client.get("/api/diff/rows")

    assert r.status_code == 200
    body = r.json()
    assert body["identical"] is False
    assert any(f["filename"] == "ROW2.txt" for f in body["files"])


def test_diff_batch_table_no_difference(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})

    r = client.get("/api/diff/rows")

    assert r.status_code == 200
    assert r.json()["identical"] is True


def test_restore_batch_table_preview_and_confirm(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})

    preview = client.post("/api/restore", json={"table_name": "rows", "snapshot_id": 1})
    assert preview.status_code == 200
    assert preview.json()["status"] == "confirmation_required"
    assert preview.json()["source"] is None
    assert len(preview.json()["sources"]) == 2

    (root / "ROW1.txt").write_text("0xFF\n")
    r = client.post("/api/restore", json={"table_name": "rows", "snapshot_id": 1, "confirm": True})

    assert r.status_code == 200
    assert r.json()["status"] == "restored"
    assert (root / "ROW1.txt").read_text() == "0x01\n"


def test_golden_set_and_get_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})
    client.put("/api/golden/rows", json={})

    r = client.get("/api/golden/rows")

    assert r.status_code == 200
    assert r.json()["status"] == "match"


def test_golden_diff_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})
    client.put("/api/golden/rows", json={})

    r = client.get("/api/golden/rows/diff")

    assert r.status_code == 200
    assert r.json()["diffs"] == {}


def test_report_shows_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)
    client.post("/api/build", json={"source": "rows"})
    client.post("/api/commit", json={"message": "v1"})

    r = client.get("/api/report")

    assert r.status_code == 200
    rows_entry = next(t for t in r.json()["tables"] if t["name"] == "rows")
    assert rows_entry["is_batch"] is True
    assert rows_entry["source_count"] == 2
    assert rows_entry["resolved_reader"] is None
    assert rows_entry["has_sidecar"] is False


def test_export_includes_batch_member_files(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    r = client.get("/api/export")

    assert r.status_code == 200
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert "ROW1.txt" in names
    assert "ROW2.txt" in names


def test_config_get_batch_table_falls_back_to_global(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    r = client.get("/api/config", params={"table": "rows"})

    assert r.status_code == 200
    assert r.json()["table"] == "rows"


def test_sidecar_routes_reject_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    assert client.get("/api/sidecar/rows").status_code == 400
    assert client.put("/api/sidecar/rows", json={"defaults": {}}).status_code == 400
    assert client.delete("/api/sidecar/rows").status_code == 400


def test_pipeline_get_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    r = client.get("/api/pipeline/rows")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["table"] == "rows"
    assert any(s["kind"] == "reader" and s["name"] == "raw_text" for s in body["stages"])
    assert body["outputs"][0].endswith("rows.bin")


def test_pipeline_get_batch_table_reports_non_terminal_writer_checkpoint(tmp_path):
    """A NON-terminal writer stage (followed by an exec) computes the
    checkpoint key with compute_pipeline_cache_key_multi for a batch
    table, instead of the single-file branch — covers that branch."""
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(
        root,
        extra=(
            'stages = ['
            '{ type = "reader", name = "raw_text" }, '
            '{ type = "writer", name = "bin" }, '
            '{ type = "exec", command = "cp {input} {output}", output_extension = ".copy" }'
            ']\n'
        ),
    )
    client = _client(root)

    r = client.get("/api/pipeline/rows")

    assert r.status_code == 200, r.text
    stages = r.json()["stages"]
    writer_stage = next(s for s in stages if s["kind"] == "writer")
    assert writer_stage["checkpoint"] == "none"


def test_pipeline_write_routes_reject_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    put_r = client.put("/api/pipeline/rows", json={"stages": [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "bin"}]})
    assert put_r.status_code == 400
    delete_r = client.delete("/api/pipeline/rows")
    assert delete_r.status_code == 400


def test_source_editor_rejects_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    assert client.get("/api/source/rows").status_code == 400
    assert client.put("/api/source/rows", json={"content": "x"}).status_code == 400
    assert client.post("/api/source/rows/validate").status_code == 400
