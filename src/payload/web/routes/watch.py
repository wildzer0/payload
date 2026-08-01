"""watch (start/stop/stream) — controparte web di 'pld watch' in
cli.py. A differenza della CLI (un ciclo bloccante, fermato da
Ctrl+C), qui l'osservazione vive in un WatchSession su app.state,
avviata/fermata da POST separate e trasmessa a chi è connesso su
GET /api/watch/stream (SSE) — vedi web/watch_session.py per il
perché non si riusa payload.watch.watch() direttamente."""
from __future__ import annotations

import json

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve
from payload.web.sse import sse_format
from payload.web.watch_session import WatchSession


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    return json.loads(raw) if raw else {}


async def watch_start(request: Request) -> JSONResponse:
    body = await _json_body(request)
    root = request.app.state.root

    session = request.app.state.watch_session
    if session is not None and session.is_running():
        return JSONResponse({"status": "already_running"})

    def _create_and_start() -> WatchSession:
        registry = load_plugins(project_root=root)
        out_dir = resolve(root, body.get("out") or "build")
        new_session = WatchSession(root, registry, out_dir, writer_name=body.get("to"))
        new_session.start()
        return new_session

    session = await anyio.to_thread.run_sync(_create_and_start)
    request.app.state.watch_session = session
    return JSONResponse({"status": "started"})


async def watch_stop(request: Request) -> JSONResponse:
    session = request.app.state.watch_session
    if session is None or not session.is_running():
        return JSONResponse({"status": "not_running"})
    await anyio.to_thread.run_sync(session.stop)
    return JSONResponse({"status": "stopped"})


async def watch_stream(request: Request) -> StreamingResponse:
    session = request.app.state.watch_session
    if session is None:
        raise InvalidRequestError("nessun watch attivo — chiama prima POST /api/watch/start")

    async def event_gen():
        q = session.subscribe()
        try:
            while True:
                event = await anyio.to_thread.run_sync(q.get)
                if event.get("__control__") == "stopped":
                    yield sse_format("stopped", "{}")
                    break
                yield sse_format("change", json.dumps(event))
        finally:
            session.unsubscribe(q)

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


ROUTES = [
    Route("/api/watch/start", watch_start, methods=["POST"]),
    Route("/api/watch/stop", watch_stop, methods=["POST"]),
    Route("/api/watch/stream", watch_stream, methods=["GET"]),
]
