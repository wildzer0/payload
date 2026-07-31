"""Test di regressione per il crash UnicodeEncodeError su console Windows
con codepage legacy (cp1252/'charmap'), che non sanno rappresentare le
emoji usate nei tip. Vedi cli.py per il fix (reconfigure errors='replace')."""
import io


def test_reconfigure_errors_replace_prevents_unicode_crash():
    """Riproduce esattamente il crash segnalato: scrivere 💡 (U+1F4A1)
    su uno stream cp1252 senza il fix solleva UnicodeEncodeError; con
    errors='replace' non solleva più, sostituisce con un placeholder."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")

    try:
        stream.write("\U0001F4A1 tip")
        raise AssertionError("doveva sollevare UnicodeEncodeError senza il fix")
    except UnicodeEncodeError:
        pass  # atteso: questo è il crash originale, riprodotto

    stream.reconfigure(errors="replace")
    stream.write("\U0001F4A1 tip")  # non deve sollevare
    stream.flush()

    buf.seek(0)
    result = buf.read().decode("cp1252")
    assert result == "? tip"


def test_reconfigure_is_noop_on_utf8_stream():
    """Su uno stream che supporta già UTF-8 (il caso comune Linux/macOS),
    il fix non deve cambiare nulla nel comportamento normale."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="utf-8", errors="strict")
    stream.reconfigure(errors="replace")

    stream.write("\U0001F4A1 tip")
    stream.flush()

    buf.seek(0)
    result = buf.read().decode("utf-8")
    assert result == "\U0001F4A1 tip"  # emoji preservata intatta, nessuna sostituzione
