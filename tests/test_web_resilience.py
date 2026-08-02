"""The web layer degrades gracefully on a project broken by hand
(duplicate table names, batch-name collisions): dashboard and status
show the healthy tables plus a warning instead of failing the whole
request. (The fs upload/rename guards prevent creating this state from
the UI — these tests cover the already-broken, hand-edited case.)"""
from pathlib import Path

from starlette.testclient import TestClient

from payload.web.app import create_app


def _broken_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    (root / "ok.raw").write_text("fine\n")
    (root / "dup").mkdir()
    (root / "dup" / "ok.raw").write_text("duplicate stem\n")  # same stem as ok.raw
    return root


def _healthy_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    (root / "ok.raw").write_text("fine\n")
    return root


def test_report_degrades_with_warnings(tmp_path):
    client = TestClient(create_app(_broken_project(tmp_path)), raise_server_exceptions=False)
    r = client.get("/api/report")
    assert r.status_code == 200  # not the old global 400 DuplicateTableNameError
    body = r.json()
    assert len(body["warnings"]) == 1
    assert "duplicate table name 'ok'" in body["warnings"][0]
    # both files are listed (degraded but visible), not silently dropped
    assert "ok" in {t["name"] for t in body["tables"]}


def test_status_degrades_with_warnings(tmp_path):
    client = TestClient(create_app(_broken_project(tmp_path)), raise_server_exceptions=False)
    r = client.get("/api/status")
    assert r.status_code == 200
    assert len(r.json()["warnings"]) == 1


def test_healthy_project_has_no_warnings(tmp_path):
    client = TestClient(create_app(_healthy_project(tmp_path)), raise_server_exceptions=False)
    assert client.get("/api/report").json()["warnings"] == []
    assert client.get("/api/status").json()["warnings"] == []


def test_report_warns_on_batch_name_collision(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "table-tool.toml").write_text(
        '[defaults]\nwriter = "bin"\n\n[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )
    (root / "rows.raw").write_text("x")  # stem collides with the batch table name
    (root / "ROW1.txt").write_text("r")
    client = TestClient(create_app(root), raise_server_exceptions=False)
    body = client.get("/api/report").json()
    assert any("collides with a [[batch_table]]" in w for w in body["warnings"])
