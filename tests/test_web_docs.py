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


def test_docs_list(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/docs")

    assert r.status_code == 200
    slugs = {d["slug"] for d in r.json()["docs"]}
    assert slugs == {"usage", "plugins", "pipeline", "batch"}


def test_doc_detail_returns_real_markdown_content(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/docs/pipeline")

    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Configurable pipeline"
    assert "reader" in body["content"]
    assert len(body["content"]) > 500


def test_doc_detail_batch_returns_real_markdown_content(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/docs/batch")

    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Batch tables"
    assert "parse_many" in body["content"]
    assert len(body["content"]) > 500


def test_doc_detail_unknown_slug_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/docs/does_not_exist")

    assert r.status_code == 404
