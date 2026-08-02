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


class NoCacheStaticFiles(StaticFiles):
    """Static files that always revalidate (Cache-Control: no-cache).

    The webapp has no versioned asset URLs (app.js, the js/ modules and
    style.css keep the same URL across releases), so without this the
    browser's heuristic caching can serve a STALE module after a
    frontend update — which shows up as confusing, half-fixed behavior
    (this has bitten twice: the module-split and the hex-view fix).
    no-cache forces a cheap If-None-Match revalidation on every load."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(root: Path) -> Starlette:
    """root: the project folder to serve — resolved by the caller
    ('pld serve' command in cli.py), every route reads it from
    request.app.state.root instead of recomputing it."""
    static_dir = _static_dir()

    async def index(request):
        response = FileResponse(static_dir / "index.html")
        response.headers["Cache-Control"] = "no-cache"  # revalidate the shell too
        return response

    async def health(request):
        # project.name is always set for a project created with
        # 'pld init' (see init_cmd.py), but a hand-written
        # table-tool.toml, or one created before this field existed,
        # can have it empty — in that case the fallback (folder
        # basename) is decided here, not in the core config, which
        # shouldn't invent missing data on its own.
        project = load_config(request.app.state.root).project
        project_name = project.name or request.app.state.root.name
        from payload._version import __version__

        return JSONResponse({
            "status": "ok",
            "root": str(request.app.state.root),
            "project_name": project_name,
            "project_description": project.description,
            "version": __version__,
        })

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/health", health),
            *ROUTES,
            Mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static"),
        ],
        exception_handlers=EXCEPTION_HANDLERS,
    )
    app.state.root = root
    return app
