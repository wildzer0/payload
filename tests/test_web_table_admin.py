"""Web tests for /api/table/delete and /api/table/import — web
counterpart of 'pld rm'/'pld import' in cli.py, same pattern
(TestClient) as test_web_history.py."""
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


def _add_batch_table(root: Path, name="rows", sources='["ROW1.txt", "ROW2.txt"]'):
    (root / "table-tool.toml").write_text(
        (root / "table-tool.toml").read_text() + f'\n[[batch_table]]\nname = "{name}"\nsources = {sources}\n'
    )


# --- /api/table/delete -------------------------------------------------------

def test_delete_missing_table_name_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/delete", json={})

    assert r.status_code == 400


def test_delete_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/delete", json={"table_name": "does_not_exist"})

    assert r.status_code == 404


def test_delete_requires_confirmation(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "first"})

    r = client.post("/api/table/delete", json={"table_name": "example_table"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "confirmation_required"
    assert body["is_batch"] is False
    assert body["dirty"] is False
    assert (root / "example_table.raw").exists()


def test_delete_confirmation_flags_dirty(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "first"})
    (root / "example_table.raw").write_text("modified after the commit\n")

    r = client.post("/api/table/delete", json={"table_name": "example_table"})

    assert r.json()["dirty"] is True


def test_delete_with_confirm_removes_source_output_cache(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})

    r = client.post("/api/table/delete", json={"table_name": "example_table", "confirm": True})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "deleted"
    assert len(body["removed_sources"]) == 1
    assert len(body["removed_outputs"]) == 1
    assert body["batch_entry_removed"] is False
    assert not (root / "example_table.raw").exists()
    assert not (root / "build" / "example_table.bin").exists()


def test_delete_keeps_history_intact(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": "example_table.raw", "to": "bin"})
    client.post("/api/commit", json={"message": "first"})

    client.post("/api/table/delete", json={"table_name": "example_table", "confirm": True})

    log = client.get("/api/log/example_table").json()
    assert len(log["snapshots"]) == 1


def test_delete_batch_table_whole_removes_config_entry(tmp_path):
    root = _init_project(tmp_path)
    (root / "ROW1.txt").write_text("0x01\n")
    (root / "ROW2.txt").write_text("0x02\n")
    _add_batch_table(root)
    client = _client(root)

    preview = client.post("/api/table/delete", json={"table_name": "rows"})
    assert preview.json()["is_batch"] is True

    r = client.post("/api/table/delete", json={"table_name": "rows", "confirm": True})

    assert r.json()["batch_entry_removed"] is True
    assert not (root / "ROW1.txt").exists()
    assert not (root / "ROW2.txt").exists()


def test_delete_member_requires_batch_table(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/delete", json={"table_name": "example_table", "member": "example_table.raw"})

    assert r.status_code == 400


def test_delete_member_unknown_filename_404(tmp_path):
    root = _init_project(tmp_path)
    (root / "ROW1.txt").write_text("0x01\n")
    (root / "ROW2.txt").write_text("0x02\n")
    _add_batch_table(root)
    client = _client(root)

    r = client.post("/api/table/delete", json={"table_name": "rows", "member": "does_not_exist.txt"})

    assert r.status_code == 404


def test_delete_member_requires_confirmation_then_removes_only_that_file(tmp_path):
    root = _init_project(tmp_path)
    (root / "ROW1.txt").write_text("0x01\n")
    (root / "ROW2.txt").write_text("0x02\n")
    _add_batch_table(root)
    client = _client(root)

    preview = client.post("/api/table/delete", json={"table_name": "rows", "member": "ROW1.txt"})
    assert preview.json()["status"] == "confirmation_required"
    assert (root / "ROW1.txt").exists()

    r = client.post("/api/table/delete", json={"table_name": "rows", "member": "ROW1.txt", "confirm": True})

    assert r.json()["batch_entry_removed"] is False
    assert not (root / "ROW1.txt").exists()
    assert (root / "ROW2.txt").exists()


def test_delete_last_member_removes_batch_entry(tmp_path):
    root = _init_project(tmp_path)
    (root / "ROW1.txt").write_text("0x01\n")
    _add_batch_table(root, sources='["ROW1.txt"]')
    client = _client(root)

    r = client.post("/api/table/delete", json={"table_name": "rows", "member": "ROW1.txt", "confirm": True})

    assert r.json()["batch_entry_removed"] is True


# --- /api/table/import -------------------------------------------------------

def test_import_no_file_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/import", data={})

    assert r.status_code == 400


def test_import_single_file_creates_table(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/import", files={"file": ("external.raw", b"0x01, 0x02\n")})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created"
    assert body["kind"] == "single"
    assert (root / "external.raw").read_bytes() == b"0x01, 0x02\n"


def test_import_single_file_custom_name(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post(
        "/api/table/import",
        files={"file": ("external.raw", b"0x01\n")},
        data={"as_name": "custom"},
    )

    assert r.status_code == 200
    assert (root / "custom.raw").exists()
    assert not (root / "external.raw").exists()


def test_import_single_file_collision_without_overwrite_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/table/import", files={"file": ("external.raw", b"0x01\n")})

    r = client.post("/api/table/import", files={"file": ("external.raw", b"0x02\n")})

    assert r.status_code == 400
    assert r.json()["error"] == "TableAlreadyExistsError"


def test_import_single_file_overwrite_true_updates(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/table/import", files={"file": ("external.raw", b"0x01\n")})

    r = client.post(
        "/api/table/import",
        files={"file": ("external.raw", b"0x02\n")},
        data={"overwrite": "true"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert (root / "external.raw").read_bytes() == b"0x02\n"


def test_import_single_file_empty_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/import", files={"file": ("external.raw", b"")})

    assert r.status_code == 400
    assert r.json()["error"] == "EmptySourceError"
    assert not (root / "external.raw").exists()


def test_import_unreadable_extension_422(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/table/import", files={"file": ("external.mysteryext", b"x")})

    assert r.status_code == 404  # NoReaderFoundError -> NotFoundError -> 404


def test_import_multiple_files_without_new_batch_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post(
        "/api/table/import",
        files=[("file", ("ROW1.txt", b"0x01\n")), ("file", ("ROW2.txt", b"0x02\n"))],
    )

    assert r.status_code == 400


def test_import_new_batch_creates_batch_table(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post(
        "/api/table/import",
        files=[("file", ("ROW1.txt", b"0x01\n")), ("file", ("ROW2.txt", b"0x02\n"))],
        data={"new_batch": "rows"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created"
    assert body["kind"] == "batch"
    assert body["name"] == "rows"
    assert (root / "ROW1.txt").exists() and (root / "ROW2.txt").exists()

    status = client.get("/api/status").json()
    assert any(t["name"] == "rows" and t["is_batch"] for t in status["tables"])


def test_import_batch_member_unknown_batch_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post(
        "/api/table/import",
        files={"file": ("ROW3.txt", b"0x03\n")},
        data={"batch": "does_not_exist"},
    )

    assert r.status_code == 404


def test_import_batch_member_adds_to_existing_batch(tmp_path):
    root = _init_project(tmp_path)
    (root / "ROW1.txt").write_text("0x01\n")
    (root / "ROW2.txt").write_text("0x02\n")
    _add_batch_table(root)
    client = _client(root)

    r = client.post(
        "/api/table/import",
        files={"file": ("ROW3.txt", b"0x03\n")},
        data={"batch": "rows"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "added"
    assert body["kind"] == "batch_member"
    assert (root / "ROW3.txt").exists()

    status = client.get("/api/status").json()
    row = next(t for t in status["tables"] if t["name"] == "rows")
    assert row["source_count"] == 3
