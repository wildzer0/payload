"""Web: the project activity timeline (/api/log/activity) and the events
emitted by builds, commits, golden changes and file-browser mutations."""
from pathlib import Path

from starlette.testclient import TestClient
from typer.testing import CliRunner

from payload.cli import app as cli_app
from payload.core.activity import log_event
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


def _events(client: TestClient) -> list[dict]:
    return client.get("/api/log/activity").json()["events"]


# ---------- route ----------

def test_activity_route_empty(tmp_path):
    client = _client(_init_project(tmp_path))
    body = client.get("/api/log/activity").json()
    assert body["events"] == []
    assert body["total"] == 0


def test_activity_route_newest_first_with_paging(tmp_path):
    root = _init_project(tmp_path)
    for i in range(4):
        log_event(root, "build", f"event {i}")
    client = _client(root)
    body = client.get("/api/log/activity").json()
    assert body["total"] == 4
    assert body["events"][0]["detail"] == "event 3"  # newest first

    page = client.get("/api/log/activity?limit=2&offset=1").json()
    assert [e["detail"] for e in page["events"]] == ["event 2", "event 1"]
    assert page["total"] == 4


def test_activity_route_bad_limit(tmp_path):
    client = _client(_init_project(tmp_path))
    assert client.get("/api/log/activity?limit=abc").status_code == 400


# ---------- emissions ----------

def test_build_and_commit_emit_events(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_and_commit(client)
    kinds = [e["kind"] for e in _events(client)]
    assert "build" in kinds
    assert "commit" in kinds


def test_golden_set_and_clear_emit_events(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    _build_and_commit(client)
    client.put("/api/golden/example_table", json={})
    client.delete("/api/golden/example_table")
    kinds = [e["kind"] for e in _events(client)]
    assert kinds.count("golden") == 2


def test_fs_write_emits_event(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/fs/write", json={"path": "note.txt", "content": "hi"})
    events = _events(client)
    assert any(e["kind"] == "fs" and "edited" in e["detail"] for e in events)


def test_fs_create_rename_copy_delete_upload_emit_events(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/fs/create", json={"path": "docs", "type": "dir"})
    client.post("/api/fs/create", json={"path": "docs/a.txt", "type": "file"})
    client.post("/api/fs/rename", json={"path": "docs/a.txt", "new_path": "docs/b.txt"})
    client.post("/api/fs/copy", json={"path": "docs/b.txt", "new_path": "docs/c.txt"})
    client.post("/api/fs/upload", data={"dir": "docs"}, files=[("file", ("x.txt", b"x", "text/plain"))])
    client.post("/api/fs/delete", json={"path": "docs/c.txt", "confirm": True})
    events = _events(client)
    kinds = [e["kind"] for e in events]
    assert kinds.count("fs") >= 6
    details = " | ".join(e["detail"] for e in events)
    assert "created dir" in details
    assert "renamed" in details
    assert "copied" in details
    assert "uploaded" in details
    assert "deleted" in details
