"""Browser-based table source editor: reads and writes the source file
directly (CSV, plain text, C, ...) for text formats — a file that
doesn't decode as UTF-8 (a binary blob, e.g. .bin) is flagged as not
editable instead of risking a broken byte-for-byte round-trip through
a <textarea>."""
from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.config import load_config
from payload.core.discovery import discover_for_history, resolve_table_ref
from payload.core.errors import TableNotFoundError
from payload.core.registry import load_plugins
from payload.testing import check_reader_behavior, check_reader_structure
from payload.web.errors import InvalidRequestError


def _find_source(sources: list[Path], batch_tables: list, table_name: str) -> Path:
    """Batch tables (N files) don't have ONE source to show in a
    single-file editor — see src/payload/docs/BATCH.md, source editing
    isn't supported for these tables in v1."""
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        raise TableNotFoundError(table_name)
    if ref.is_batch:
        raise InvalidRequestError(
            f"'{table_name}' is a batch table ({len(ref.source_paths)} files): "
            "the source editor only supports single-file tables"
        )
    return ref.source_paths[0]


async def source_get(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        src = _find_source(sources, batch_tables, table)
        raw_bytes = src.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "table": table, "path": str(src), "editable": False,
                "reason": "the file isn't UTF-8 text, probably a binary format",
            }
        return {"table": table, "path": str(src), "editable": True, "content": text}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def source_put(request: Request) -> JSONResponse:
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        raise InvalidRequestError("missing 'content' parameter or it isn't a string")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        src = _find_source(sources, batch_tables, table)
        src.write_text(content, encoding="utf-8")
        return {"table": table, "path": str(src), "saved": True, "size": src.stat().st_size}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def source_validate(request: Request) -> JSONResponse:
    """Revalidates the source file with the reader that would REALLY
    be chosen for a build (config.defaults.reader if set, otherwise
    auto-resolved from extension/sniff — same priority as
    resolve_pipeline_spec) — the exact same conformance checks as
    'plugin validate' (check_reader_structure + check_reader_behavior),
    just pointed at the table's real file instead of a separate
    sample: so a syntax error introduced by editing the content from
    the browser (or a default reader that can't read this file) shows
    up right away, without waiting for a build."""
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        src = _find_source(sources, batch_tables, table)
        table_config = load_config(root, source_path=src)
        registry = load_plugins(strict=False, project_root=root)
        reader = registry.find_reader(src, table_config.defaults.reader)
        issues = check_reader_structure(reader) + check_reader_behavior(reader, src)
        return {
            "table": table, "reader": reader.name,
            "conforms": not issues,
            "issues": [{"check": i.check, "detail": i.detail} for i in issues],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/source/{table_name}", source_get, methods=["GET"]),
    Route("/api/source/{table_name}", source_put, methods=["PUT"]),
    Route("/api/source/{table_name}/validate", source_validate, methods=["POST"]),
]
