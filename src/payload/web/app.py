"""
Factory dell'app Starlette per 'pld serve'. create_app(root) collega:
gestione errori centralizzata (vedi errors.py), file statici del
frontend e tutte le route API."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from payload.web.errors import EXCEPTION_HANDLERS
from payload.web.routes import ROUTES


def _static_dir() -> Path:
    return Path(str(importlib.resources.files("payload.web") / "static"))


def create_app(root: Path) -> Starlette:
    """root: cartella del progetto da servire — risolta dal chiamante
    (comando 'pld serve' in cli.py), ogni route la legge da
    request.app.state.root invece di ricalcolarla."""
    static_dir = _static_dir()

    async def index(request):
        return FileResponse(static_dir / "index.html")

    async def health(request):
        return JSONResponse({"status": "ok", "root": str(request.app.state.root)})

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/health", health),
            *ROUTES,
            Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
        ],
        exception_handlers=EXCEPTION_HANDLERS,
    )
    app.state.root = root
    return app
