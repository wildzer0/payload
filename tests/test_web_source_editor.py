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
    new_content = "0x2A, 0x2B          # aggiornato dalla web UI\n"

    r = client.put("/api/source/example_table", json={"content": new_content})

    assert r.status_code == 200
    assert r.json()["saved"] is True
    assert (root / "example_table.raw").read_text() == new_content


def test_get_reports_non_editable_binary_source(tmp_path):
    """Un file scoperto come sorgente (estensione nota, .c qui) ma con
    byte non decodificabili come UTF-8 — succede se un reader binario
    condivide l'estensione con un formato testuale, o più semplicemente
    con un file corrotto/non testuale piazzato lì per errore."""
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

    r = client.get("/api/source/non_esiste")

    assert r.status_code == 404


def test_put_missing_content_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/source/example_table", json={})

    assert r.status_code == 400


def test_put_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/source/non_esiste", json={"content": "x"})

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
    client.put("/api/source/example_table", json={"content": "questo non e' hex valido\n"})

    r = client.post("/api/source/example_table/validate")

    assert r.status_code == 200
    body = r.json()
    assert body["conforms"] is False
    assert body["issues"]


def test_validate_respects_configured_reader_default(tmp_path):
    """Regressione: validate ignorava config.defaults.reader e validava
    sempre col reader auto-risolto, riportando indietro il reader
    sbagliato quando l'utente ne aveva impostato uno esplicito da
    sidecar — sembrava che l'override 'non fosse stato salvato'."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"reader": "csv"}})

    r = client.post("/api/source/example_table/validate")

    assert r.status_code == 200
    body = r.json()
    assert body["reader"] == "csv"
    assert body["conforms"] is False  # il contenuto di example_table.raw non è CSV valido


def test_validate_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/source/non_esiste/validate")

    assert r.status_code == 404
