"""
Test unitari di payload/watch.py — a differenza di tests/test_watch.py
(livello CLI, con watch_loop mockato a no-op per non bloccare), questi
esercitano davvero _DebouncedTableHandler e watch() (con Observer/
time.sleep mockati, così il loop bloccante non gira per sempre nei
test)."""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from payload.watch import _DebouncedTableHandler, watch


class _FakeEvent:
    def __init__(self, src_path, is_directory=False):
        self.src_path = src_path
        self.is_directory = is_directory


def _handler(tmp_path, on_change=None, debounce=0.03):
    out_dir = tmp_path / "build"
    return _DebouncedTableHandler(
        known_extensions={".raw"}, output_dir=out_dir,
        on_change=on_change or MagicMock(), debounce_seconds=debounce,
    ), out_dir


def test_should_handle_filters_unknown_extension(tmp_path):
    h, _ = _handler(tmp_path)
    assert h._should_handle(str(tmp_path / "t.txt")) is None


def test_should_handle_excludes_output_dir(tmp_path):
    h, out_dir = _handler(tmp_path)
    out_dir.mkdir()
    inside = out_dir / "generato.raw"
    inside.write_text("x")
    assert h._should_handle(str(inside)) is None


def test_should_handle_returns_path_for_valid_file(tmp_path):
    h, _ = _handler(tmp_path)
    src = tmp_path / "t.raw"
    src.write_text("x")
    assert h._should_handle(str(src)) == src


def test_should_handle_tolerates_unresolvable_path(tmp_path):
    h, _ = _handler(tmp_path)
    src = tmp_path / "t.raw"
    src.write_text("x")
    real_resolve = Path.resolve

    def fake_resolve(self, *a, **kw):
        if self == src:
            raise OSError("simulato")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", fake_resolve):
        assert h._should_handle(str(src)) == src


def test_on_modified_ignores_directory_events(tmp_path):
    on_change = MagicMock()
    h, _ = _handler(tmp_path, on_change=on_change, debounce=0.02)
    h.on_modified(_FakeEvent(str(tmp_path / "sub"), is_directory=True))
    time.sleep(0.1)
    on_change.assert_not_called()


def test_on_modified_schedules_and_calls_on_change_after_debounce(tmp_path):
    on_change = MagicMock()
    h, _ = _handler(tmp_path, on_change=on_change, debounce=0.02)
    src = tmp_path / "t.raw"
    src.write_text("x")

    h.on_modified(_FakeEvent(str(src)))
    on_change.assert_not_called()  # non ancora, il debounce non è scaduto

    time.sleep(0.1)
    on_change.assert_called_once_with(src)


def test_rapid_successive_events_debounced_into_one_call(tmp_path):
    on_change = MagicMock()
    h, _ = _handler(tmp_path, on_change=on_change, debounce=0.05)
    src = tmp_path / "t.raw"
    src.write_text("x")

    for _ in range(5):
        h.on_modified(_FakeEvent(str(src)))
        time.sleep(0.01)

    time.sleep(0.15)
    on_change.assert_called_once_with(src)


def test_on_created_delegates_to_on_modified(tmp_path):
    on_change = MagicMock()
    h, _ = _handler(tmp_path, on_change=on_change, debounce=0.02)
    src = tmp_path / "t.raw"
    src.write_text("x")

    h.on_created(_FakeEvent(str(src)))
    time.sleep(0.1)
    on_change.assert_called_once_with(src)


def test_fire_skips_if_file_deleted_before_timer_fires(tmp_path):
    on_change = MagicMock()
    h, _ = _handler(tmp_path, on_change=on_change, debounce=0.05)
    src = tmp_path / "t.raw"
    src.write_text("x")

    h.on_modified(_FakeEvent(str(src)))
    src.unlink()

    time.sleep(0.15)
    on_change.assert_not_called()


# --- watch() -----------------------------------------------------------

def test_watch_starts_observer_and_stops_cleanly_on_keyboard_interrupt(tmp_path):
    fake_observer = MagicMock()

    with patch("payload.watch.Observer", return_value=fake_observer), \
         patch("payload.watch.time.sleep", side_effect=KeyboardInterrupt):
        watch(tmp_path, {".raw"}, tmp_path / "build", on_change=MagicMock())

    fake_observer.schedule.assert_called_once()
    fake_observer.start.assert_called_once()
    fake_observer.stop.assert_called_once()
    fake_observer.join.assert_called_once()


def test_watch_wraps_on_change_errors_without_propagating(tmp_path):
    """Un errore nel callback on_change non deve mai far crashare il
    watch — solo loggato, la logica sta nel wrapper _safe_on_change
    interno a watch()."""
    fake_observer = MagicMock()
    captured_handler = {}

    def _schedule(handler, path, recursive):
        captured_handler["handler"] = handler

    fake_observer.schedule.side_effect = _schedule

    def _raising_on_change(path):
        raise RuntimeError("boom")

    with patch("payload.watch.Observer", return_value=fake_observer), \
         patch("payload.watch.time.sleep", side_effect=KeyboardInterrupt):
        watch(tmp_path, {".raw"}, tmp_path / "build", on_change=_raising_on_change)

    # il wrapper passato all'handler non deve propagare l'eccezione
    captured_handler["handler"]._on_change(tmp_path / "t.raw")
