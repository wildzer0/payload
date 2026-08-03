"""Batch tables management — web counterpart of the CLI's batch flows
('pld import --new-batch', config writes). Reads the [[batch_table]]
list and mutates it (create / whole-list save / delete) through the
same core helpers the CLI uses."""
from __future__ import annotations

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.batch_tables import resolve_batch_tables
from payload.core.config import (
    create_batch_table,
    load_config,
    remove_batch_table_entry,
    upsert_batch_table,
)
from payload.core.discovery import discover_table_sources, is_table_candidate
from pathlib import Path

from payload.web.errors import InvalidRequestError


def _batch_members(root, base) -> set[Path]:
    batches = resolve_batch_tables(root, base)
    return {p.resolve() for b in batches for p in b.source_paths}


def _tracked_tables(root) -> set[str]:
    """Tables that have saved history (snapshots). Only these would be
    shown as 'deleted but restorable' on the dashboard if their source
    file stopped being a single table — a fresh, never-committed .raw
    file joining a batch loses nothing and must stay allowed."""
    from payload.core.history import HistoryStore

    return set(HistoryStore(root).all_tracked_tables())


def _clean_sources(sources, root) -> list[str]:
    if not isinstance(sources, list) or not all(isinstance(s, str) and s for s in sources):
        raise InvalidRequestError("'sources' must be a non-empty list of strings")
    base = load_config(root)
    members = _batch_members(root, base)
    tracked = _tracked_tables(root)
    out = []
    for s in sources:
        s = s.strip().lstrip("/")
        if not s or s in (".", "..") or s.startswith("../") or ".." in s.split("/"):
            raise InvalidRequestError(f"invalid source path '{s}'")
        # a batch member must be a table source (config, sidecars, hidden
        # files and internal dirs are rejected — a .config.toml is not a
        # table), and it must exist (resolve_batch_tables refuses missing
        # members at build time anyway)
        if not is_table_candidate(root / s, root, base.defaults.output_dir, base.defaults.cache_dir):
            raise InvalidRequestError(f"'{s}' is not a table source file")
        # A tracked single-file table can't silently become a batch
        # member: discovery would drop it from the live tables and the
        # dashboard would show it as 'deleted but restorable'. Files
        # with no history (never built/committed) are fine to fold in.
        if Path(s).stem in tracked and (root / s).resolve() not in members:
            raise InvalidRequestError(
                f"'{s}' already belongs to the single-file table '{Path(s).stem}' — "
                "a file can't be both a single table and a batch member "
                "(drop the table first, or pick another file)"
            )
        out.append(s)
    return out


def _optional_str(body, key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRequestError(f"'{key}' must be a string")
    return value


async def batch_list_route(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        base_config = load_config(root)
        batches = resolve_batch_tables(root, base_config)
        return {
            "batches": [
                {
                    "name": b.name,
                    "sources": [str(sp.relative_to(root)) for sp in b.source_paths],
                    "reader": b.reader,
                    "writer": b.writer,
                    "byte_order": b.byte_order,
                }
                for b in batches
            ]
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def batch_create_route(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequestError("missing 'name' parameter")
    root = request.app.state.root
    sources = _clean_sources(body.get("sources"), root)

    def _run():
        create_batch_table(
            root, name.strip(),
            sources,
            reader=_optional_str(body, "reader"),
            writer=_optional_str(body, "writer"),
            byte_order=_optional_str(body, "byte_order"),
        )
        return {"name": name.strip(), "sources": sources}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def batch_update_route(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    body = await request.json()
    if "sources" not in body:
        raise InvalidRequestError("missing 'sources' parameter")
    root = request.app.state.root
    sources = _clean_sources(body.get("sources"), root)

    def _run():
        upsert_batch_table(
            root, name,
            sources,
            reader=_optional_str(body, "reader"),
            writer=_optional_str(body, "writer"),
            byte_order=_optional_str(body, "byte_order"),
        )
        return {"name": name, "sources": sources}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def batch_delete_route(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    root = request.app.state.root

    def _run():
        removed = remove_batch_table_entry(root, name)
        return {"name": name, "removed": removed}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def batch_candidates_route(request: Request) -> JSONResponse:
    """Every file that could be a table source (same predicate as table
    discovery: excludes config, sidecars, hidden files, plugins/ and
    the output/cache dirs) — feeds the batch member picker so it shows
    only real candidate files, not the whole directory."""
    root = request.app.state.root

    def _run():
        base = load_config(root)
        members = _batch_members(root, base)
        tracked = _tracked_tables(root)
        sources = [
            p for p in discover_table_sources(root, base.defaults.output_dir, base.defaults.cache_dir)
            if p.resolve() in members or p.stem not in tracked
        ]
        return {"files": [str(p.relative_to(root)) for p in sources]}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    # literal path BEFORE the /{name} route so "candidates" isn't captured
    Route("/api/batch/candidates", batch_candidates_route, methods=["GET"]),
    Route("/api/batch", batch_list_route, methods=["GET"]),
    Route("/api/batch", batch_create_route, methods=["POST"]),
    Route("/api/batch/{name}", batch_update_route, methods=["PUT"]),
    Route("/api/batch/{name}", batch_delete_route, methods=["DELETE"]),
]
