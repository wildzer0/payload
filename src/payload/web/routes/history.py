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

from payload.core.batch_tables import effective_config
from payload.core.config import load_config
from payload.core.discovery import all_table_refs, discover_for_history, resolve_table_ref
from payload.core.errors import NothingToCommitError, SnapshotNotFoundError, TableNotFoundError
from payload.core.history import HistoryStore, legacy_compatible_source_blobs
from payload.core.pipeline import describe_table_build
from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


def _find_ref(sources: list[Path], batch_tables: list, table_name: str):
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        raise TableNotFoundError(table_name)
    return ref


async def status(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        sources, batch_tables, base_config = discover_for_history(root)
        history = HistoryStore(root)
        tables = []
        for ref in all_table_refs(sources, batch_tables):
            table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
            out_dir = resolve(root, table_config.defaults.output_dir)
            output_paths = list(out_dir.glob(f"{ref.name}.*")) if out_dir.exists() else []
            last = history.last_snapshot(ref.name)
            if last is None:
                state = "never_saved"
            elif history.is_dirty(ref.name, ref.source_paths, output_paths):
                state = "dirty"
            else:
                state = "clean"
            tables.append({
                "name": ref.name,
                "path": str(ref.source_paths[0]) if not ref.is_batch else None,
                "is_batch": ref.is_batch,
                "source_count": len(ref.source_paths),
                "state": state,
            })
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
        sources, batch_tables, base_config = discover_for_history(root)
        history = HistoryStore(root)
        registry = load_plugins(project_root=root)
        output_dir = resolve(root, base_config.defaults.output_dir)

        target_tables = all_table_refs(sources, batch_tables)
        if only:
            target_tables = [t for t in target_tables if t.name in only]

        dirty = []
        for ref in target_tables:
            output_paths = list(output_dir.glob(f"{ref.name}.*"))
            if history.is_dirty(ref.name, ref.source_paths, output_paths):
                table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
                build_info = describe_table_build(
                    ref.source_paths, registry, table_config, output_paths, output_dir, table_name=ref.name,
                )
                dirty.append((ref, output_paths, build_info))
        if not dirty:
            raise NothingToCommitError()

        committed = []
        for ref, output_paths, build_info in dirty:
            snap = history.commit(ref.name, ref.source_paths, output_paths, message, **build_info)
            committed.append({
                "name": ref.name,
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
        sources, batch_tables, _ = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table_name)

        comparable_blobs = legacy_compatible_source_blobs(ref.source_paths, snap.source_blobs)
        files = []
        for src in ref.source_paths:
            current = src.read_bytes()
            blob_hash = comparable_blobs.get(src.name)
            expected = history.read_blob(blob_hash) if blob_hash else b""
            if current == expected:
                continue
            chunks = []
            max_len = max(len(current), len(expected))
            for i in range(0, max_len, 8):
                c_chunk, e_chunk = current[i:i + 8], expected[i:i + 8]
                if c_chunk != e_chunk:
                    chunks.append({"offset": i, "current": c_chunk.hex(" "), "snapshot": e_chunk.hex(" ")})
            files.append({"filename": src.name, "chunks": chunks})

        if not files:
            return {"snapshot_id": snap_id, "identical": True, "chunks": [], "files": []}
        return {
            "snapshot_id": snap_id, "identical": False,
            # 'chunks' del primo file diverso, per retrocompatibilità col
            # frontend a singolo-file esistente; 'files' è la vista
            # completa multi-file (usata per le tabelle batch).
            "chunks": files[0]["chunks"],
            "files": files,
        }

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

        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table_name)

        if not confirm:
            return {
                "status": "confirmation_required",
                "source": str(ref.source_paths[0]) if not ref.is_batch else None,
                "sources": [str(p) for p in ref.source_paths],
                "snapshot_id": snapshot_id,
            }

        result = history.restore(table_name, snapshot_id, ref.source_paths, resolve(root, config.defaults.output_dir))
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
        sources, batch_tables, _ = discover_for_history(root)
        _find_ref(sources, batch_tables, table)  # valida che la tabella esista
        history = HistoryStore(root)
        snap = history.get_snapshot(table, snapshot_id)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, blob_hash in snap.source_blobs.items():
                # <source> è il placeholder legacy (vedi
                # SnapshotMeta.from_dict) per snapshot pre-tabelle-batch
                # che non conoscevano il filename reale — usa il nome
                # tabella come fallback, meglio di un file letteralmente
                # chiamato '<source>' nello zip.
                zf.writestr(filename if filename != "<source>" else table, history.read_blob(blob_hash))
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
