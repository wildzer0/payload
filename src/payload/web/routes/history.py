"""status / commit / log / diff / restore — controparte web dei comandi
omonimi in cli.py, stessa suddivisione di responsabilità."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from payload.core.config import load_config
from payload.core.discovery import discover_for_history
from payload.core.errors import NothingToCommitError, SnapshotNotFoundError, TableNotFoundError
from payload.core.history import HistoryStore
from payload.core.pipeline import describe_table_build
from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


def _find_source(sources: list[Path], table_name: str) -> Path:
    src = next((s for s in sources if s.stem == table_name), None)
    if src is None:
        raise TableNotFoundError(table_name)
    return src


async def status(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        sources, _ = discover_for_history(root)
        history = HistoryStore(root)
        tables = []
        for src in sources:
            name = src.stem
            table_config = load_config(root, source_path=src)
            out_dir = resolve(root, table_config.defaults.output_dir)
            output_paths = list(out_dir.glob(f"{name}.*")) if out_dir.exists() else []
            last = history.last_snapshot(name)
            if last is None:
                state = "never_saved"
            elif history.is_dirty(name, src, output_paths):
                state = "dirty"
            else:
                state = "clean"
            tables.append({"name": name, "path": str(src), "state": state})
        return {"tables": tables}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def commit(request: Request) -> JSONResponse:
    body = await request.json()
    message = body.get("message")
    if not message:
        raise InvalidRequestError("parametro 'message' mancante")
    only = body.get("only")
    root = request.app.state.root

    def _run():
        sources, config = discover_for_history(root)
        history = HistoryStore(root)
        registry = load_plugins(project_root=root)
        output_dir = resolve(root, config.defaults.output_dir)

        target_sources = sources
        if only:
            target_sources = [s for s in sources if s.stem in only]

        dirty = []
        for s in target_sources:
            output_paths = list(output_dir.glob(f"{s.stem}.*"))
            if history.is_dirty(s.stem, s, output_paths):
                table_config = load_config(root, source_path=s)
                build_info = describe_table_build(s, registry, table_config, output_paths, output_dir)
                dirty.append((s, output_paths, build_info))
        if not dirty:
            raise NothingToCommitError()

        committed = []
        for src, output_paths, build_info in dirty:
            snap = history.commit(src.stem, src, output_paths, message, **build_info)
            committed.append({
                "name": src.stem,
                "snapshot_id": snap.id,
                "outputs": len(snap.output_blobs),
                "missing_outputs": snap.missing_outputs,
            })
        return {"committed": committed}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


DEFAULT_LOG_PAGE_SIZE = 8
MAX_LOG_PAGE_SIZE = 200


async def log_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table_name = request.path_params.get("table_name")
    try:
        limit = int(request.query_params.get("limit", DEFAULT_LOG_PAGE_SIZE))
        offset = int(request.query_params.get("offset", 0))
    except ValueError:
        raise InvalidRequestError("'limit'/'offset' devono essere numeri interi")
    limit = max(1, min(limit, MAX_LOG_PAGE_SIZE))
    offset = max(0, offset)

    def _run():
        history = HistoryStore(root)
        # più recente prima, come mostrato in UI/CLI: la pagina 0 deve
        # contenere gli snapshot più freschi, non i più vecchi.
        snapshots = list(reversed(history.log(table_name)))
        page = snapshots[offset:offset + limit]
        return {
            "table": table_name,
            "head_snapshot_id": history.head_snapshot_id(table_name),
            "tip_snapshot_id": history.tip_snapshot_id(table_name),
            "total": len(snapshots),
            "has_more": offset + limit < len(snapshots),
            "snapshots": [
                {
                    "id": s.id,
                    "timestamp": s.timestamp,
                    "message": s.message,
                    "outputs": list(s.output_blobs.keys()),
                    "reader": s.reader,
                    "writers": s.writers,
                    "pipeline_explicit": s.pipeline_explicit,
                    "pipeline_description": s.pipeline_description,
                    "missing_outputs": s.missing_outputs,
                }
                for s in page
            ],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def tracked_tables(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        return {"tables": HistoryStore(root).all_tracked_tables()}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def diff_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table_name = request.path_params["table_name"]
    snapshot_param = request.query_params.get("snapshot")

    def _run():
        history = HistoryStore(root)
        if snapshot_param is not None:
            snap_id = int(snapshot_param)
        else:
            last = history.last_snapshot(table_name)
            if last is None:
                raise SnapshotNotFoundError(table_name, reason="nessuno snapshot salvato per questa tabella")
            snap_id = last.id

        snap = history.get_snapshot(table_name, snap_id)
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table_name)

        current = src.read_bytes()
        expected = history.read_blob(snap.source_blob)
        if current == expected:
            return {"snapshot_id": snap_id, "identical": True, "chunks": []}

        chunks = []
        max_len = max(len(current), len(expected))
        for i in range(0, max_len, 8):
            c_chunk, e_chunk = current[i:i + 8], expected[i:i + 8]
            if c_chunk != e_chunk:
                chunks.append({"offset": i, "current": c_chunk.hex(" "), "snapshot": e_chunk.hex(" ")})
        return {"snapshot_id": snap_id, "identical": False, "chunks": chunks}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def restore_route(request: Request) -> JSONResponse:
    body = await request.json()
    table_name = body.get("table_name")
    snapshot_id = body.get("snapshot_id")
    confirm = bool(body.get("confirm", False))
    if not table_name or snapshot_id is None:
        raise InvalidRequestError("parametri 'table_name'/'snapshot_id' mancanti")
    root = request.app.state.root

    def _run():
        history = HistoryStore(root)
        history.get_snapshot(table_name, snapshot_id)  # valida che esista, solleva se manca

        sources, config = discover_for_history(root)
        src = _find_source(sources, table_name)

        if not confirm:
            return {"status": "confirmation_required", "source": str(src), "snapshot_id": snapshot_id}

        result = history.restore(table_name, snapshot_id, src, resolve(root, config.defaults.output_dir))
        return {
            "status": "restored",
            "written": [str(w) for w in result.written],
            "removed": [str(r) for r in result.removed],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def snapshot_download_route(request: Request) -> Response:
    """Zip di uno snapshot (sorgente + ogni output allegato), ricostruito
    al volo dai blob content-addressed — nessun file temporaneo da
    ripulire, lo snapshot di una singola tabella è sempre piccolo."""
    root = request.app.state.root
    table = request.path_params["table_name"]
    try:
        snapshot_id = int(request.path_params["snapshot_id"])
    except ValueError:
        raise InvalidRequestError("snapshot_id deve essere un numero intero")

    def _run() -> bytes:
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        history = HistoryStore(root)
        snap = history.get_snapshot(table, snapshot_id)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(src.name, history.read_blob(snap.source_blob))
            for filename, blob_hash in snap.output_blobs.items():
                zf.writestr(filename, history.read_blob(blob_hash))
        return buf.getvalue()

    data = await anyio.to_thread.run_sync(_run)
    filename = f"{table}-snapshot-{snapshot_id}.zip"
    return Response(
        data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


ROUTES = [
    Route("/api/status", status, methods=["GET"]),
    Route("/api/commit", commit, methods=["POST"]),
    Route("/api/log", tracked_tables, methods=["GET"]),
    Route("/api/log/{table_name}", log_route, methods=["GET"]),
    Route("/api/log/{table_name}/{snapshot_id}/download", snapshot_download_route, methods=["GET"]),
    Route("/api/diff/{table_name}", diff_route, methods=["GET"]),
    Route("/api/restore", restore_route, methods=["POST"]),
]
