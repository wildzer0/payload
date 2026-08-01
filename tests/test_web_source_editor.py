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


def test_get_editable_text_source(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/source/example_table")

    assert r.status_code == 200
    body = r.json()
    assert body["editable"] is True
    assert body["content"] == (root / "example_table.raw").read_text()


def test_put_updates_source(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    new_content = "0x2A, 0x2B          # updated from the web UI\n"

    r = client.put("/api/source/example_table", json={"content": new_content})

    assert r.status_code == 200
    assert r.json()["saved"] is True
    assert (root / "example_table.raw").read_text() == new_content


def test_get_reports_non_editable_binary_source(tmp_path):
    """A file discovered as a source (known extension, .c here) but
    with bytes that don't decode as UTF-8 — happens if a binary reader
    shares the extension with a text format, or more simply with a
    corrupted/non-text file placed there by mistake."""
    root = _init_project(tmp_path)
    (root / "weird.c").write_bytes(b"\xff\xfe\x00\x01binary-ish\x80\x90")
    client = _client(root)

    r = client.get("/api/source/weird")

    assert r.status_code == 200
    body = r.json()
    assert body["editable"] is False
    assert "reason" in body
    assert "content" not in body


def test_get_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/source/does_not_exist")

    assert r.status_code == 404


def test_put_missing_content_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/source/example_table", json={})

    assert r.status_code == 400


def test_put_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/source/does_not_exist", json={"content": "x"})

    assert r.status_code == 404


# --- validate ---


def test_validate_conforms(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/source/example_table/validate")

    assert r.status_code == 200
    body = r.json()
    assert body["reader"] == "raw_text"
    assert body["conforms"] is True
    assert body["issues"] == []


def test_validate_reports_parse_error_after_bad_edit(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/source/example_table", json={"content": "this is not valid hex\n"})

    r = client.post("/api/source/example_table/validate")

    assert r.status_code == 200
    body = r.json()
    assert body["conforms"] is False
    assert body["issues"]


def test_validate_respects_configured_reader_default(tmp_path):
    """Regression: validate used to ignore config.defaults.reader and
    always validated with the auto-resolved reader, reporting back the
    wrong reader when the user had set one explicitly via sidecar — it
    looked like the override 'hadn't been saved'."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"reader": "csv"}})

    r = client.post("/api/source/example_table/validate")

    assert r.status_code == 200
    body = r.json()
    assert body["reader"] == "csv"
    assert body["conforms"] is False  # example_table.raw's content isn't valid CSV


def test_validate_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/source/does_not_exist/validate")

    assert r.status_code == 404
