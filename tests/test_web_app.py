"""Test dello scaffolding di payload.web: create_app(), gestione errori
centralizzata, file statici. Le route API vere e proprie hanno i loro
file dedicati (tests/test_web_*.py)."""
from pathlib import Path

from starlette.testclient import TestClient

from payload.core.errors import (
    GoldenMismatchError,
    GoldenMissingError,
    GoldenStaleError,
    InvalidConfigError,
    NoReaderFoundError,
    NothingToCommitError,
    PluginApiVersionError,
    PluginLoadError,
    ReaderParseError,
    SnapshotNotFoundError,
)
from payload.web.app import create_app


def _client_for(exc: Exception):
    from starlette.applications import Starlette
    from starlette.routing import Route

    from payload.web.errors import EXCEPTION_HANDLERS

    async def boom(request):
        raise exc

    app = Starlette(routes=[Route("/boom", boom)], exception_handlers=EXCEPTION_HANDLERS)
    return TestClient(app, raise_server_exceptions=False)


def test_health_reports_configured_root(tmp_path):
    client = TestClient(create_app(tmp_path))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "root": str(tmp_path)}


def test_index_serves_frontend_shell(tmp_path):
    client = TestClient(create_app(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_static_files_are_served(tmp_path):
    client = TestClient(create_app(tmp_path))
    r = client.get("/static/index.html")
    assert r.status_code == 200


def test_not_found_error_maps_to_404():
    client = _client_for(NoReaderFoundError(Path("t.unknown")))
    r = client.get("/boom")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "NoReaderFoundError"
    assert "hint" in body


def test_build_error_maps_to_422():
    client = _client_for(ReaderParseError(Path("t.raw"), "valore non valido"))
    assert client.get("/boom").status_code == 422


def test_config_error_maps_to_400():
    client = _client_for(InvalidConfigError(Path("table-tool.toml"), field="x", reason="y"))
    assert client.get("/boom").status_code == 400


def test_golden_mismatch_maps_to_409():
    client = _client_for(GoldenMismatchError("example_table"))
    assert client.get("/boom").status_code == 409


def test_golden_missing_overridden_to_404():
    client = _client_for(GoldenMissingError("example_table"))
    assert client.get("/boom").status_code == 404


def test_golden_stale_maps_to_409():
    client = _client_for(GoldenStaleError("example_table"))
    assert client.get("/boom").status_code == 409


def test_snapshot_not_found_maps_to_404():
    client = _client_for(SnapshotNotFoundError("t", reason="mai salvata"))
    assert client.get("/boom").status_code == 404


def test_nothing_to_commit_overridden_to_409():
    client = _client_for(NothingToCommitError())
    assert client.get("/boom").status_code == 409


def test_plugin_load_error_overridden_to_500():
    client = _client_for(PluginLoadError("mio_plugin", "payload.readers", "boom"))
    assert client.get("/boom").status_code == 500


def test_plugin_api_version_error_overridden_to_500():
    client = _client_for(PluginApiVersionError("mio_plugin", "2.0", "1.0"))
    assert client.get("/boom").status_code == 500


def test_unexpected_exception_becomes_generic_500():
    client = _client_for(RuntimeError("bug interno mai previsto"))
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "InternalError"
    assert "bug interno" not in body["message"]  # niente dettagli interni esposti al client
