"""Test dello stream SSE di build-all — usa una queue.Queue/threading.Thread
reali (vedi routes/build.py), quindi questi test esercitano davvero il
ponte di concorrenza, non lo mockano."""
import json
from pathlib import Path
from unittest.mock import patch

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


def _parse_sse(lines: list[str]) -> list[tuple[str, dict]]:
    events = []
    event_name = None
    for line in lines:
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: "):])))
    return events


def test_build_all_stream_emits_progress_then_summary(tmp_path):
    root = _init_project(tmp_path)
    (root / "second.raw").write_text("0x02\n")
    client = _client(root)

    with client.stream("GET", "/api/build-all/stream") as r:
        assert r.status_code == 200
        lines = [l for l in r.iter_lines() if l]

    events = _parse_sse(lines)
    progress_events = [e for e in events if e[0] == "progress"]
    summary_events = [e for e in events if e[0] == "summary"]

    assert len(progress_events) == 2
    assert len(summary_events) == 1
    assert summary_events[0][1]["built"] == 2
    assert (root / "build" / "example_table.bin").exists()
    assert (root / "build" / "second.bin").exists()


def test_build_all_stream_reports_duplicate_names_as_error_event(tmp_path):
    root = _init_project(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "example_table.raw").write_text("0x01\n")
    client = _client(root)

    with client.stream("GET", "/api/build-all/stream") as r:
        lines = [l for l in r.iter_lines() if l]

    events = _parse_sse(lines)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"] == "DuplicateTableNameError"


def test_build_all_stream_reports_real_failure_in_summary(tmp_path):
    root = _init_project(tmp_path)
    (root / "broken.raw").write_text("0xZZ\n")
    client = _client(root)

    with client.stream("GET", "/api/build-all/stream") as r:
        lines = [l for l in r.iter_lines() if l]

    events = _parse_sse(lines)
    summary = next(e[1] for e in events if e[0] == "summary")
    assert summary["errors"] == 1
    assert len(summary["failures"]) == 1


def test_build_all_stream_reports_unexpected_exception_as_error_event(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    with patch("payload.web.routes.build.run_batch_build", side_effect=RuntimeError("bug interno")):
        with client.stream("GET", "/api/build-all/stream") as r:
            lines = [l for l in r.iter_lines() if l]

    events = _parse_sse(lines)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"] == "InternalError"


def test_build_all_stream_respects_query_params(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    with client.stream("GET", "/api/build-all/stream", params={"jobs": "2", "to": "bin"}) as r:
        lines = [l for l in r.iter_lines() if l]

    events = _parse_sse(lines)
    summary = next(e[1] for e in events if e[0] == "summary")
    assert summary["built"] == 1
    assert (root / "build" / "example_table.bin").exists()


def test_build_all_stream_rejects_excessive_jobs(tmp_path):
    """jobs alimenta ThreadPoolExecutor(max_workers=jobs) senza alcun
    tetto naturale — un valore assurdo (typo o voluto) non deve poter
    tentare di allocare centinaia di migliaia di thread."""
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/build-all/stream", params={"jobs": "999999999"})

    assert r.status_code == 400


def test_build_all_stream_rejects_non_numeric_jobs(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/build-all/stream", params={"jobs": "abc"})

    assert r.status_code == 400
