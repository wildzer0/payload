"""golden get/set/clear/diff — web counterpart of the same-named
commands in cli.py, same split of responsibilities.

Golden here is a pointer to a snapshot already recorded in
HistoryStore, no longer a separate frozen file — every route is
therefore per-table (no longer per-output-path like in the old
version)."""
from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.batch_tables import effective_config
from payload.core.config import load_config
from payload.core.discovery import discover_for_history, resolve_table_ref
from payload.core.errors import TableNotFoundError
from payload.core.golden import check_golden, clear_golden, golden_diff, set_golden
from payload.core.history import HistoryStore
from payload.web.paths import resolve


def _find_ref(sources: list[Path], batch_tables: list, table_name: str):
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        raise TableNotFoundError(table_name)
    return ref


def _output_paths(root: Path, base_config, ref) -> list[Path]:
    table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
    out_dir = resolve(root, table_config.defaults.output_dir)
    return list(out_dir.glob(f"{ref.name}.*")) if out_dir.exists() else []


async def golden_get_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        history = HistoryStore(root)
        output_paths = _output_paths(root, config, ref)
        result = check_golden(history, table, ref.source_paths, output_paths)
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
        sources, batch_tables, _ = discover_for_history(root)
        _find_ref(sources, batch_tables, table)  # validates that the table exists
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
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        history = HistoryStore(root)
        output_paths = _output_paths(root, config, ref)
        diffs = golden_diff(history, table, output_paths)
        return {"table": table, "diffs": diffs}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/golden/{table_name}", golden_get_route, methods=["GET"]),
    Route("/api/golden/{table_name}", golden_set_route, methods=["PUT"]),
    Route("/api/golden/{table_name}", golden_clear_route, methods=["DELETE"]),
    Route("/api/golden/{table_name}/diff", golden_diff_route, methods=["GET"]),
]
