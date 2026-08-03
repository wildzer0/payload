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


def test_pipeline_write_routes_accept_batch_table(tmp_path):
    """Batches carry their pipeline inline in [[batch_table]] — the
    write routes accept them (this used to be rejected because the
    routes were written for the sidecar path only)."""
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    put_r = client.put("/api/pipeline/rows", json={"stages": [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "bin"}]})
    assert put_r.status_code == 200
    assert put_r.json()["explicit"] is True
    delete_r = client.delete("/api/pipeline/rows")
    assert delete_r.status_code == 200
    assert delete_r.json()["explicit"] is False


def test_source_editor_rejects_batch_table(tmp_path):
    root = _init_project(tmp_path)
    _write_rows(root)
    _add_batch_table(root)
    client = _client(root)

    assert client.get("/api/source/rows").status_code == 400
    assert client.put("/api/source/rows", json={"content": "x"}).status_code == 400
    assert client.post("/api/source/rows/validate").status_code == 400


def _make_batch(root, name, sources):
    from payload.core.config import create_batch_table

    create_batch_table(root, name, sources)
    return root


def test_batch_crud_roundtrip(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n", encoding="utf-8")
    (root / "b.raw").write_text("# b\n", encoding="utf-8")

    # list starts empty
    assert client.get("/api/batch").json()["batches"] == []

    # create
    r = client.post("/api/batch", json={"name": "sensors", "sources": ["a.raw", "b.raw"], "byte_order": "big"})
    assert r.status_code == 200
    batches = client.get("/api/batch").json()["batches"]
    assert len(batches) == 1
    assert batches[0]["name"] == "sensors"
    assert batches[0]["sources"] == ["a.raw", "b.raw"]
    assert batches[0]["byte_order"] == "big"

    # update (whole-list replace + clear byte_order)
    r = client.put("/api/batch/sensors", json={"sources": ["a.raw"], "reader": "raw_text", "byte_order": ""})
    assert r.status_code == 200
    batches = client.get("/api/batch").json()["batches"]
    assert batches[0]["sources"] == ["a.raw"]
    assert batches[0]["reader"] == "raw_text"
    assert batches[0]["byte_order"] is None

    # invalid sources rejected
    assert client.post("/api/batch", json={"name": "bad", "sources": ["../escape"]}).status_code == 400
    assert client.post("/api/batch", json={"name": "bad", "sources": []}).status_code == 400

    # delete
    r = client.delete("/api/batch/sensors")
    assert r.status_code == 200
    assert r.json()["removed"] is True
    assert client.get("/api/batch").json()["batches"] == []
    # deleting again is a no-op
    assert client.delete("/api/batch/sensors").json()["removed"] is False


def test_batch_validation_errors(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n", encoding="utf-8")
    assert client.post("/api/batch", json={"sources": ["a.raw"]}).status_code == 400       # missing name
    assert client.post("/api/batch", json={"name": "x", "sources": ["a.raw"], "reader": 123}).status_code == 400  # reader not a string
    assert client.post("/api/batch", json={"name": "x", "sources": ["a.raw"], "byte_order": 7}).status_code == 400  # byte_order not a string
    assert client.put("/api/batch/x", json={}).status_code == 400                          # missing sources
    assert client.post("/api/batch", json={"name": "x", "sources": [".."]}).status_code == 400  # traversal
    assert client.post("/api/batch", json={"name": "x", "sources": ["a/../b"]}).status_code == 400  # embedded traversal
    assert client.post("/api/batch", json={"name": "x", "sources": "a.raw"}).status_code == 400  # sources not a list


def test_upsert_batch_table_new_name(tmp_path):
    from payload.core.config import upsert_batch_table, load_config
    from payload.core.batch_tables import resolve_batch_tables

    root = _init_project(tmp_path)
    (root / "a.raw").write_text("# a\n", encoding="utf-8")
    upsert_batch_table(root, "fresh", ["a.raw"], reader="raw_text", byte_order="")
    batches = resolve_batch_tables(root, load_config(root))
    assert batches[0].name == "fresh"
    assert batches[0].reader == "raw_text"
    assert batches[0].byte_order is None


def test_batch_rejects_non_table_sources(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n", encoding="utf-8")
    (root / "side.config.toml").write_text("[defaults]\n", encoding="utf-8")
    (root / "table-tool.toml").write_text("[[batch_table]]\nname='x'\nsources=['a.raw']\n", encoding="utf-8")

    # a real sidecar is not a table source
    r = client.post("/api/batch", json={"name": "bad", "sources": ["side.config.toml"]})
    assert r.status_code == 400
    assert "not a table source" in str(r.json())

    # missing files are refused too (they'd fail at build anyway)
    assert client.post("/api/batch", json={"name": "bad", "sources": ["missing.raw"]}).status_code == 400

    # a genuine candidate passes
    assert client.post("/api/batch", json={"name": "ok", "sources": ["a.raw"]}).status_code == 200


def test_batch_candidates_endpoint(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n", encoding="utf-8")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    (root / "side.config.toml").write_text("[defaults]\n", encoding="utf-8")
    (root / "build" / "out.bin").write_bytes(b"x")  # build/ already exists from init

    files = client.get("/api/batch/candidates").json()["files"]
    assert "a.raw" in files and "b.txt" in files
    assert "side.config.toml" not in files
    assert "build/out.bin" not in files
    assert not any("table-tool.toml" == f for f in files)


def _tracked_table_project(tmp_path):
    """Project where example_table has real history (built + committed)."""
    root = _init_project(tmp_path)
    client = _client(root)
    r = client.post("/api/build", json={"source": str(root / "example_table.raw")})
    assert r.status_code == 200, r.text
    r = client.post("/api/commit", json={"message": "v1", "only": ["example_table"]})
    assert r.status_code == 200, r.text
    return root


def test_batch_create_rejects_tracked_single_table_sources(tmp_path):
    """A file that backs a tracked single-file table can't become a batch
    member: discovery would silently orphan the table (still in history,
    gone from disk) and the dashboard would offer to restore it."""
    root = _tracked_table_project(tmp_path)
    client = _client(root)

    r = client.post("/api/batch", json={"name": "sensors", "sources": ["example_table.raw"]})
    assert r.status_code == 400
    assert "already belongs to the single-file table" in r.text

    # the table is untouched and still live (not orphaned)
    live = {t["name"] for t in client.get("/api/report").json()["tables"]}
    assert "example_table" in live
    tracked = client.get("/api/log").json()["tables"]
    assert "example_table" in tracked  # still tracked, not orphaned


def test_batch_accepts_fresh_files_even_if_tracked(tmp_path):
    """A fresh .raw file (no history yet) can freely join a batch."""
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "extra.raw").write_text("# extra\n0x01,\n")

    r = client.post("/api/batch", json={"name": "sensors", "sources": ["extra.raw"]})
    assert r.status_code == 200


def test_batch_candidates_hide_tracked_single_table_sources(tmp_path):
    root = _tracked_table_project(tmp_path)
    client = _client(root)
    (root / "extra.raw").write_text("# extra\n0x01,\n")

    files = client.get("/api/batch/candidates").json()["files"]
    assert "extra.raw" in files                # a free file is offered
    assert "example_table.raw" not in files    # a tracked table's source is hidden


def test_batch_pipeline_round_trip(tmp_path):
    """A batch table can carry its own pipeline: the stages live inline
    in the [[batch_table]] entry (no sidecar), survive a GET, and are
    cleared by DELETE — same editor, same UX as single tables."""
    root = _init_project(tmp_path)
    client = _client(root)
    (root / "a.raw").write_text("# a\n0x01,\n")
    (root / "b.raw").write_text("# b\n0x02,\n")
    r = client.post("/api/batch", json={"name": "sensors", "sources": ["a.raw", "b.raw"]})
    assert r.status_code == 200

    # GET resolves the implicit 2-stage pipeline from the batch defaults
    r = client.get("/api/pipeline/sensors")
    assert r.status_code == 200
    assert [s["kind"] for s in r.json()["stages"]] == ["reader", "writer"]

    # PUT an explicit pipeline (same payload shape as single tables)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    r = client.put("/api/pipeline/sensors", json={"stages": stages})
    assert r.status_code == 200
    assert r.json()["explicit"] is True

    # persisted inline in [[batch_table]], NOT in a sidecar
    config_text = (root / "table-tool.toml").read_text()
    assert "stages" in config_text
    assert not (root / "a.config.toml").exists()
    assert not (root / "b.config.toml").exists()

    # GET returns it, DELETE clears it
    assert client.get("/api/pipeline/sensors").json()["explicit"] is True
    r = client.delete("/api/pipeline/sensors")
    assert r.status_code == 200
    assert r.json()["explicit"] is False
    assert "stages" not in (root / "table-tool.toml").read_text()
