"""Test unitari di WatchSession — debounce_seconds piccolo per non
dover attendere i ~0.3s reali di default ad ogni assert (stesso motivo
per cui tests/test_watch_core.py fa lo stesso con
_DebouncedTableHandler)."""
import time
from pathlib import Path

from payload.core.registry import PluginRegistry
from payload.web.watch_session import WatchSession
from tests.fakes import BrokenReader, FakeReader, FakeWriter


def _session(root: Path, debounce=0.02) -> WatchSession:
    registry = PluginRegistry()
    registry.register_reader(FakeReader())
    registry.register_writer(FakeWriter())
    return WatchSession(root, registry, root / "build", writer_name="fake_writer", debounce_seconds=debounce)


def test_not_running_before_start(tmp_path):
    session = _session(tmp_path)
    assert session.is_running() is False


def test_start_is_idempotent(tmp_path):
    session = _session(tmp_path)
    session.start()
    session.start()  # non deve sollevare né creare un secondo observer
    assert session.is_running() is True
    session.stop()


def test_stop_before_start_is_a_noop(tmp_path):
    session = _session(tmp_path)
    session.stop()  # non deve sollevare
    assert session.is_running() is False


def test_file_change_triggers_build_and_notifies_subscriber(tmp_path):
    src = tmp_path / "t.fake"
    src.write_text("ciao")
    session = _session(tmp_path)
    q = session.subscribe()
    session.start()
    try:
        src.write_text("modificato")
        event = q.get(timeout=2)
    finally:
        session.stop()

    assert event["status"] == "ok"
    assert event["source"] == str(src)
    assert (tmp_path / "build" / "t.fakeout").exists()


def test_stop_notifies_all_subscribers_with_stopped_sentinel(tmp_path):
    session = _session(tmp_path)
    q1, q2 = session.subscribe(), session.subscribe()
    session.start()

    session.stop()

    assert q1.get(timeout=1) == {"__control__": "stopped"}
    assert q2.get(timeout=1) == {"__control__": "stopped"}


def test_file_change_with_parse_error_notifies_error_event(tmp_path):
    src = tmp_path / "t.broken"
    src.write_text("irrilevante")
    registry = PluginRegistry()
    registry.register_reader(BrokenReader())
    registry.register_writer(FakeWriter())
    session = WatchSession(tmp_path, registry, tmp_path / "build", writer_name="fake_writer", debounce_seconds=0.02)
    q = session.subscribe()
    session.start()
    try:
        src.write_text("ancora irrilevante, ma diverso")
        event = q.get(timeout=2)
    finally:
        session.stop()

    assert event["status"] == "error"
    assert event["error"] == "ReaderParseError"


def test_unsubscribe_stops_receiving_events(tmp_path):
    src = tmp_path / "t.fake"
    src.write_text("ciao")
    session = _session(tmp_path)
    q = session.subscribe()
    session.unsubscribe(q)
    session.start()
    try:
        src.write_text("modificato")
        time.sleep(0.15)
    finally:
        session.stop()

    assert q.empty()
