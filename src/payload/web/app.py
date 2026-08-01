"""
Starlette app factory for 'pld serve'. create_app(root) wires up:
centralized error handling (see errors.py), the frontend's static
files, and all the API routes."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from payload.core.config import load_config
from payload.web.errors import EXCEPTION_HANDLERS
from payload.web.routes import ROUTES


def _static_dir() -> Path:
    return Path(str(importlib.resources.files("payload.web") / "static"))


def create_app(root: Path) -> Starlette:
    """root: the project folder to serve — resolved by the caller
    ('pld serve' command in cli.py), every route reads it from
    request.app.state.root instead of recomputing it."""
    static_dir = _static_dir()

    async def index(request):
        return FileResponse(static_dir / "index.html")

    async def health(request):
        # project.name is always set for a project created with
        # 'pld init' (see init_cmd.py), but a hand-written
        # table-tool.toml, or one created before this field existed,
        # can have it empty — in that case the fallback (folder
        # basename) is decided here, not in the core config, which
        # shouldn't invent missing data on its own.
        project = load_config(request.app.state.root).project
        project_name = project.name or request.app.state.root.name
        return JSONResponse({
            "status": "ok",
            "root": str(request.app.state.root),
            "project_name": project_name,
            "project_description": project.description,
        })

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
