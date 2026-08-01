"""Formattazione Server-Sent Events — usato da build-all e watch, gli
unici due endpoint che trasmettono aggiornamenti live (vedi
routes/build.py, routes/watch.py)."""
from __future__ import annotations


def sse_format(event: str, data: str) -> str:
    """'data' deve essere già una stringa su una riga sola (es. JSON
    compatto, senza newline interni) — il protocollo SSE tratta ogni
    riga che inizia con 'data:' come un pezzo separato del payload."""
    return f"event: {event}\ndata: {data}\n\n"
