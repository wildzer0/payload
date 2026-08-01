"""Test HTTP delle route /api/watch/* — WatchSession stessa è già
testata a fondo in tests/test_web_watch_session.py con debounce
piccolo; qui verifichiamo solo l'orchestrazione HTTP (start/stop
idempotenti, stream che riceve gli eventi, shutdown pulito)."""
import threading
import time
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


def test_stream_without_active_session_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/watch/stream")

    assert r.status_code == 400


def test_stop_without_active_session_is_noop(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/watch/stop")

    assert r.status_code == 200
    assert r.json()["status"] == "not_running"


def test_start_then_start_again_is_idempotent(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    first = client.post("/api/watch/start")
    second = client.post("/api/watch/start")

    assert first.json()["status"] == "started"
    assert second.json()["status"] == "already_running"

    client.post("/api/watch/stop")


def test_stream_receives_change_event_on_file_modification(tmp_path):
    root = _init_project(tmp_path)
    app = create_app(root)
    stream_client = TestClient(app)
    action_client = TestClient(app)

    action_client.post("/api/watch/start")

    def _modify_then_stop():
        time.sleep(0.5)  # oltre il debounce di default (0.3s) di WatchSession
        (root / "example_table.raw").write_text("0x99\n")
        time.sleep(0.5)
        action_client.post("/api/watch/stop")

    threading.Thread(target=_modify_then_stop, daemon=True).start()

    with stream_client.stream("GET", "/api/watch/stream") as r:
        lines = [l for l in r.iter_lines() if l]

    assert any(l.startswith("event: change") for l in lines)
    assert any(l.startswith("event: stopped") for l in lines)


def test_start_stop_stream_receives_stopped_event(tmp_path):
    """TestClient consuma lo stream fino in fondo prima di ridare il
    controllo (non è un vero streaming incrementale come un browser
    reale) — per esercitare 'stop arriva mentre lo stream è aperto'
    serve quindi un thread separato che chiama lo stop mentre il
    thread principale è bloccato a leggere lo stream."""
    root = _init_project(tmp_path)
    app = create_app(root)
    stream_client = TestClient(app)
    action_client = TestClient(app)

    action_client.post("/api/watch/start")

    def _stop_soon():
        time.sleep(0.2)
        action_client.post("/api/watch/stop")

    threading.Thread(target=_stop_soon, daemon=True).start()

    with stream_client.stream("GET", "/api/watch/stream") as r:
        assert r.status_code == 200
        lines = [l for l in r.iter_lines() if l]

    assert any(l.startswith("event: stopped") for l in lines)


def test_watch_session_torn_down_on_app_shutdown(tmp_path):
    root = _init_project(tmp_path)
    app = create_app(root)
    with TestClient(app) as client:
        client.post("/api/watch/start")
        assert app.state.watch_session.is_running() is True

    # TestClient come context manager esegue il ciclo di vita lifespan
    # completo (startup+shutdown) — allo shutdown, la sessione non deve
    # restare attiva (nessun thread watchdog residuo dopo Ctrl+C su
    # 'pld serve').
    assert app.state.watch_session.is_running() is False
