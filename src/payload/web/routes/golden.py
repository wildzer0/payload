"""golden get/set/clear/diff — controparte web dei comandi omonimi in
cli.py, stessa suddivisione di responsabilità.

Golden qui è un puntatore a uno snapshot già registrato in
HistoryStore, non più un file frozen separato — ogni route è quindi
per-tabella (non più per-path-di-output come nella vecchia versione)."""
from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.config import load_config
from payload.core.discovery import discover_for_history
from payload.core.errors import TableNotFoundError
from payload.core.golden import check_golden, clear_golden, golden_diff, set_golden
from payload.core.history import HistoryStore
from payload.web.paths import resolve


def _find_source(sources: list[Path], table_name: str) -> Path:
    src = next((s for s in sources if s.stem == table_name), None)
    if src is None:
        raise TableNotFoundError(table_name)
    return src


def _output_paths(root: Path, table_name: str, src: Path) -> list[Path]:
    table_config = load_config(root, source_path=src)
    out_dir = resolve(root, table_config.defaults.output_dir)
    return list(out_dir.glob(f"{table_name}.*")) if out_dir.exists() else []


async def golden_get_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        history = HistoryStore(root)
        output_paths = _output_paths(root, table, src)
        result = check_golden(history, table, src, output_paths)
        return {
            "table": table, "status": result.status,
            "golden_snapshot_id": result.golden_snapshot_id,
            "golden_message": result.golden_message,
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def golden_set_route(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    snapshot_id = body.get("snapshot_id")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        _find_source(sources, table)  # valida che la tabella esista
        history = HistoryStore(root)
        golden_id = set_golden(history, table, snapshot_id)
        return {"table": table, "golden_snapshot_id": golden_id}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def golden_clear_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        history = HistoryStore(root)
        cleared = clear_golden(history, table)
        return {"table": table, "status": "cleared" if cleared else "not_set"}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def golden_diff_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        history = HistoryStore(root)
        output_paths = _output_paths(root, table, src)
        diffs = golden_diff(history, table, output_paths)
        return {"table": table, "diffs": diffs}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/golden/{table_name}", golden_get_route, methods=["GET"]),
    Route("/api/golden/{table_name}", golden_set_route, methods=["PUT"]),
    Route("/api/golden/{table_name}", golden_clear_route, methods=["DELETE"]),
    Route("/api/golden/{table_name}/diff", golden_diff_route, methods=["GET"]),
]
