"""Regression test for the UnicodeEncodeError crash on Windows consoles
with a legacy codepage (cp1252/'charmap'), which can't represent the
emoji used in the tips. See cli.py for the fix (reconfigure errors='replace')."""
import io


def test_reconfigure_errors_replace_prevents_unicode_crash():
    """Reproduces exactly the reported crash: writing 💡 (U+1F4A1) to a
    cp1252 stream without the fix raises UnicodeEncodeError; with
    errors='replace' it no longer raises, it substitutes a placeholder."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")

    try:
        stream.write("\U0001F4A1 tip")
        raise AssertionError("should have raised UnicodeEncodeError without the fix")
    except UnicodeEncodeError:
        pass  # expected: this is the original crash, reproduced

    stream.reconfigure(errors="replace")
    stream.write("\U0001F4A1 tip")  # must not raise
    stream.flush()

    buf.seek(0)
    result = buf.read().decode("cp1252")
    assert result == "? tip"


def test_reconfigure_is_noop_on_utf8_stream():
    """On a stream that already supports UTF-8 (the common Linux/macOS
    case), the fix must not change anything in normal behavior."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="utf-8", errors="strict")
    stream.reconfigure(errors="replace")

    stream.write("\U0001F4A1 tip")
    stream.flush()

    buf.seek(0)
    result = buf.read().decode("utf-8")
    assert result == "\U0001F4A1 tip"  # emoji preserved intact, no substitution
