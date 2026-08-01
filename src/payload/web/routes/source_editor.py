"""Editor del contenuto sorgente di una tabella dal browser: lettura e
scrittura diretta del file sorgente (CSV, testo grezzo, C, ...) per i
formati testuali — un file che non decodifica come UTF-8 (blob binario,
es. .bin) viene segnalato come non modificabile invece di rischiare un
round-trip byte-per-byte sbagliato attraverso una <textarea>."""
from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.config import load_config
from payload.core.discovery import discover_for_history
from payload.core.errors import TableNotFoundError
from payload.core.registry import load_plugins
from payload.testing import check_reader_behavior, check_reader_structure
from payload.web.errors import InvalidRequestError


def _find_source(sources: list[Path], table_name: str) -> Path:
    src = next((s for s in sources if s.stem == table_name), None)
    if src is None:
        raise TableNotFoundError(table_name)
    return src


async def source_get(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        raw_bytes = src.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "table": table, "path": str(src), "editable": False,
                "reason": "il file non è testo UTF-8, probabilmente un formato binario",
            }
        return {"table": table, "path": str(src), "editable": True, "content": text}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def source_put(request: Request) -> JSONResponse:
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        raise InvalidRequestError("parametro 'content' mancante o non è una stringa")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        src.write_text(content, encoding="utf-8")
        return {"table": table, "path": str(src), "saved": True, "size": src.stat().st_size}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def source_validate(request: Request) -> JSONResponse:
    """Rivalida il file sorgente col reader che verrebbe scelto DAVVERO
    per una build (config.defaults.reader se impostato, altrimenti
    auto-risoluzione da estensione/sniff — stessa priorità di
    resolve_pipeline_spec) — stessi identici check di conformità di
    'plugin validate' (check_reader_structure + check_reader_behavior),
    solo puntati sul file vero della tabella invece che su un sample a
    parte: così un errore di sintassi introdotto modificando il
    contenuto dal browser (o un reader di default che non sa leggere
    questo file) si vede subito, senza aspettare un build."""
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
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
