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


def test_status_never_saved(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/status")

    assert r.status_code == 200
    tables = r.json()["tables"]
    assert any(t["name"] == "example_table" and t["state"] == "never_saved" for t in tables)


def test_status_clean_and_dirty_states(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    clean = {t["name"]: t["state"] for t in client.get("/api/status").json()["tables"]}
    assert clean["example_table"] == "clean"

    (root / "example_table.raw").write_text("0x99\n")
    dirty = {t["name"]: t["state"] for t in client.get("/api/status").json()["tables"]}
    assert dirty["example_table"] == "dirty"


def test_status_duplicate_names_error(tmp_path):
    root = _init_project(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "example_table.raw").write_text("0x01\n")
    client = _client(root)

    r = client.get("/api/status")

    assert r.status_code == 400  # DuplicateTableNameError, ConfigError -> 400


def test_commit_requires_message(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/commit", json={})

    assert r.status_code == 400


def test_commit_nothing_dirty_conflict(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.post("/api/commit", json={"message": "niente di nuovo"})

    assert r.status_code == 409  # NothingToCommitError overridden -> 409


def test_commit_sees_change_when_only_writer_changed(tmp_path):
    """Regressione: build con writer 'bin', commit, poi rebuild della
    STESSA tabella con writer 'hex' (sorgente invariato) — deve poter
    essere committata di nuovo, non risultare 'niente di nuovo'."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "con writer bin"})

    client.post("/api/build", json={"source": "example_table.raw", "to": "hex", "force": True})
    status = client.get("/api/status").json()
    row = next(t for t in status["tables"] if t["name"] == "example_table")
    assert row["state"] == "dirty"

    r = client.post("/api/commit", json={"message": "con writer hex"})

    assert r.status_code == 200
    assert r.json()["committed"][0]["name"] == "example_table"


def test_commit_only_filters_tables(tmp_path):
    root = _init_project(tmp_path)
    (root / "altra.raw").write_text("0x01\n")
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/build", json={"source": "altra.raw", "to": "bin"})

    r = client.post("/api/commit", json={"message": "solo altra", "only": ["altra"]})

    assert r.status_code == 200
    committed = [c["name"] for c in r.json()["committed"]]
    assert committed == ["altra"]


def test_log_lists_tracked_tables(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    assert client.get("/api/log").json()["tables"] == []

    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/log")
    assert r.json()["tables"] == ["example_table"]


def test_log_for_specific_table(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/log/example_table")

    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["message"] == "primo"


def _commit_n_times(client, root: Path, n: int) -> None:
    src = root / "example_table.raw"
    for i in range(n):
        src.write_text(f"0x{i:02x}\n")
        client.post("/api/build", json={"source": "example_table.raw", "to": "bin", "force": True})
        client.post("/api/commit", json={"message": f"v{i}"})


def test_log_paginates_with_default_page_size(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _commit_n_times(client, root, 10)

    r = client.get("/api/log/example_table")

    body = r.json()
    assert len(body["snapshots"]) == 8  # DEFAULT_LOG_PAGE_SIZE
    assert body["total"] == 10
    assert body["has_more"] is True
    assert body["snapshots"][0]["message"] == "v9"  # più recente prima


def test_log_pagination_offset_reaches_the_end(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _commit_n_times(client, root, 10)

    r = client.get("/api/log/example_table?limit=8&offset=8")

    body = r.json()
    assert len(body["snapshots"]) == 2
    assert body["has_more"] is False
    assert body["snapshots"][-1]["message"] == "v0"  # il più vecchio, in fondo


def test_log_invalid_limit_returns_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/log/example_table?limit=not-a-number")

    assert r.status_code == 400


def test_snapshot_download_returns_zip_with_source_and_output(tmp_path):
    import io
    import zipfile

    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/log/example_table/1/download")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "example_table-snapshot-1.zip" in r.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "example_table.raw" in names
    assert "example_table.bin" in names
    assert zf.read("example_table.raw") == (root / "example_table.raw").read_bytes()
    assert zf.read("example_table.bin") == (root / "build" / "example_table.bin").read_bytes()


def test_snapshot_download_unknown_snapshot_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/log/example_table/999/download")

    assert r.status_code == 404


def test_snapshot_download_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/log/non_esiste/1/download")

    assert r.status_code == 404


def test_snapshot_download_non_numeric_snapshot_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/log/example_table/abc/download")

    assert r.status_code == 400


def test_diff_no_snapshot_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/diff/example_table")

    assert r.status_code == 404


def test_diff_identical(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/diff/example_table")

    assert r.status_code == 200
    assert r.json()["identical"] is True


def test_diff_explicit_snapshot_param(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    r = client.get("/api/diff/example_table", params={"snapshot": "1"})

    assert r.status_code == 200
    assert r.json()["snapshot_id"] == 1


def test_diff_source_deleted_after_commit_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").unlink()

    r = client.get("/api/diff/example_table")

    assert r.status_code == 404


def test_diff_shows_chunks_when_changed(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").write_text("0x99\n")

    r = client.get("/api/diff/example_table")

    assert r.status_code == 200
    body = r.json()
    assert body["identical"] is False
    assert len(body["chunks"]) > 0


def test_restore_requires_confirmation(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").write_text("0x99\n")

    r = client.post("/api/restore", json={"table_name": "example_table", "snapshot_id": 1})

    assert r.status_code == 200
    assert r.json()["status"] == "confirmation_required"
    assert (root / "example_table.raw").read_text() == "0x99\n"  # non ancora ripristinato


def test_restore_with_confirm_writes_files(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    original = (root / "example_table.raw").read_text()
    (root / "example_table.raw").write_text("0x99\n")

    r = client.post("/api/restore", json={"table_name": "example_table", "snapshot_id": 1, "confirm": True})

    assert r.status_code == 200
    assert r.json()["status"] == "restored"
    assert (root / "example_table.raw").read_text() == original


def test_restore_leaves_table_clean_via_status(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").write_text("0x99\n")
    client.post("/api/commit", json={"message": "secondo"})

    r = client.post("/api/restore", json={"table_name": "example_table", "snapshot_id": 1, "confirm": True})
    assert r.status_code == 200

    status = client.get("/api/status").json()
    table = next(t for t in status["tables"] if t["name"] == "example_table")
    assert table["state"] == "clean"


def test_restore_does_not_create_new_snapshot(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").write_text("0x99\n")
    client.post("/api/commit", json={"message": "secondo"})

    client.post("/api/restore", json={"table_name": "example_table", "snapshot_id": 1, "confirm": True})

    log = client.get("/api/log/example_table").json()
    assert len(log["snapshots"]) == 2
    assert log["head_snapshot_id"] == 1
    assert log["tip_snapshot_id"] == 2


def test_log_reports_reader_writer_outputs_and_head(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})

    log = client.get("/api/log/example_table").json()

    assert log["head_snapshot_id"] == 1
    assert log["tip_snapshot_id"] == 1
    snap = log["snapshots"][0]
    assert snap["outputs"] == ["example_table.bin"]
    assert snap["writers"] == ["bin"]
    assert snap["reader"]


def test_commit_of_partial_fanout_reports_missing_output(tmp_path):
    """Stessa regressione di test_cli_history_commands.py ma via web:
    build parziale (writer 'obj' fallisce senza toolchain configurato,
    vedi test_obj_writer_mocked.py), poi commit deve segnalare
    esplicitamente cosa manca — sia nella risposta di /api/commit che
    permanentemente sullo snapshot via /api/log."""
    root = _init_project(tmp_path)
    (root / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }, '
        '{ type = "writer", name = "obj" }'
        ']\n'
    )
    client = _client(root)

    build_resp = client.post("/api/build", json={"source": "example_table.raw"})
    assert build_resp.status_code == 422
    assert (root / "build" / "example_table.bin").exists()
    assert not (root / "build" / "example_table.o").exists()

    commit_resp = client.post("/api/commit", json={"message": "parziale"})
    assert commit_resp.status_code == 200
    committed = commit_resp.json()["committed"][0]
    assert committed["missing_outputs"] == ["example_table.o"]

    log = client.get("/api/log/example_table").json()
    assert log["snapshots"][0]["missing_outputs"] == ["example_table.o"]


def test_log_reflects_actual_writer_used_not_config_default(tmp_path):
    """Regressione trovata dall'utente: buildare con un writer ad-hoc
    (--to header) diverso dal default risolto (bin, via raw_text) e poi
    committare deve registrare 'header' nello snapshot — non 'bin',
    che sarebbe quello che la config risolverebbe ORA (il writer
    ad-hoc di una singola build non viene mai scritto in config)."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "header"})
    client.post("/api/commit", json={"message": "con header"})

    log = client.get("/api/log/example_table").json()

    snap = log["snapshots"][0]
    assert snap["writers"] == ["header"]
    assert snap["outputs"] == ["example_table.h"]


def test_report_reflects_tip_snapshot_id(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "primo"})
    (root / "example_table.raw").write_text("0x99\n")
    client.post("/api/commit", json={"message": "secondo"})
    client.post("/api/restore", json={"table_name": "example_table", "snapshot_id": 1, "confirm": True})

    report = client.get("/api/report").json()
    row = next(t for t in report["tables"] if t["name"] == "example_table")

    assert row["last_snapshot"]["id"] == 1
    assert row["tip_snapshot_id"] == 2


def test_restore_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/restore", json={"table_name": "non_esiste", "snapshot_id": 1, "confirm": True})

    assert r.status_code == 404


def test_restore_missing_params_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/restore", json={"table_name": "example_table"})

    assert r.status_code == 400
