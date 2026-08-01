"""Server-Sent Events formatting — used by build-all and watch, the
only two endpoints that stream live updates (see routes/build.py,
routes/watch.py)."""
from __future__ import annotations


def sse_format(event: str, data: str) -> str:
    """'data' must already be a single-line string (e.g. compact JSON,
    no internal newlines) — the SSE protocol treats every line
    starting with 'data:' as a separate piece of the payload."""
    return f"event: {event}\ndata: {data}\n\n"
