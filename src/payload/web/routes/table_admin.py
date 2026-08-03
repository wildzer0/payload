"""Import (new table or update to an existing one, single or batch)
and deletion (source + output + cache, never the history) — web
counterpart of the 'pld import'/'pld rm' commands in cli.py."""
from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.cache import BuildCache
from payload.core.activity import log_event
from payload.core.clusters import resolve_clusters
from payload.core.discovery import discover_for_history, resolve_table_config, resolve_table_ref
from payload.core.errors import SourceNotFoundError, TableNotFoundError
from payload.core.history import HistoryStore
from payload.core.table_admin import (
    clone_table,
    delete_batch_member,
    delete_table,
    import_batch_member,
    import_many_single_tables,
    import_new_batch_table,
    import_single_table,
    rename_table,
)
from payload.core.table_meta import resolve_table_meta
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


def _find_ref(sources, batch_tables, table_name: str):
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        raise TableNotFoundError(table_name)
    return ref


async def delete_table_route(request: Request) -> JSONResponse:
    """Like /api/restore: without 'confirm' it returns what would be
    deleted (for an explicit confirmation dialog on the client side —
    NEVER a single-click deletion), with 'confirm: true' it actually
    performs it. The history is never touched."""
    body = await request.json()
    table_name = body.get("table_name")
    member = body.get("member")
    confirm = bool(body.get("confirm", False))
    if not table_name:
        raise InvalidRequestError("missing 'table_name' parameter")
    root = request.app.state.root

    def _run():
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table_name)
        clusters = resolve_clusters(root, config)
        table_metas = resolve_table_meta(root, config, clusters)
        table_config = resolve_table_config(root, config, ref, clusters, table_metas)
        output_dir = resolve(root, table_config.defaults.output_dir)
        cache = BuildCache(resolve(root, table_config.defaults.cache_dir))
        history = HistoryStore(root)

        if member is not None:
            if not ref.is_batch:
                raise InvalidRequestError("'member' only applies to a batch table")
            if not any(p.name == member for p in ref.source_paths):
                raise SourceNotFoundError(Path(member))
            if not confirm:
                return {"status": "confirmation_required", "table_name": table_name, "member": member}
            result = delete_batch_member(root, ref.batch, member, output_dir, cache)
        else:
            if not confirm:
                out_paths = list(output_dir.glob(f"{ref.name}.*")) if output_dir.exists() else []
                dirty = history.is_dirty(ref.name, ref.source_paths, out_paths, byte_order=table_config.defaults.byte_order)
                return {
                    "status": "confirmation_required",
                    "table_name": table_name,
                    "is_batch": ref.is_batch,
                    "sources": [str(p) for p in ref.source_paths],
                    "dirty": dirty,
                }
            result = delete_table(root, ref, output_dir, cache)

        cache.save()
        return {
            "status": "deleted",
            "removed_sources": [str(p) for p in result.removed_sources],
            "removed_outputs": [str(p) for p in result.removed_outputs],
            "batch_entry_removed": result.batch_entry_removed,
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def import_route(request: Request) -> JSONResponse:
    """multipart/form-data: one or more 'file' fields (more than one
    only together with 'new_batch' or 'each'), plus the optional
    fields 'as_name', 'batch', 'new_batch', 'each', 'overwrite' — same
    logic as 'pld import', see core/table_admin.py for the detail of
    each case."""
    root = request.app.state.root
    form = await request.form()
    uploads = form.getlist("file")
    if not uploads:
        raise InvalidRequestError("no file uploaded (missing 'file' field)")

    as_name = (form.get("as_name") or "").strip() or None
    batch = (form.get("batch") or "").strip() or None
    new_batch = (form.get("new_batch") or "").strip() or None
    each = form.get("each") == "true"
    overwrite = form.get("overwrite") == "true"

    if sum(bool(x) for x in (batch, new_batch, each)) > 1:
        raise InvalidRequestError("'batch', 'new_batch' and 'each' are mutually exclusive")

    files = [(u.filename, await u.read()) for u in uploads]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)

        if each:
            data_by_name = {name: data for name, data in files}
            result = import_many_single_tables(root, data_by_name, sources, batch_tables, overwrite=overwrite)
            return {
                "status": "created" if not result.skipped else "partial",
                "kind": "bulk",
                "imported": [{"path": str(r.path), "created": r.created} for r in result.imported],
                "skipped": [{"filename": s.filename, "reason": s.reason} for s in result.skipped],
            }

        if new_batch:
            data_by_name = {name: data for name, data in files}
            bt = import_new_batch_table(root, data_by_name, new_batch, sources, batch_tables)
            return {"status": "created", "kind": "batch", "name": bt.name, "sources": [str(p) for p in bt.source_paths]}

        if len(files) != 1:
            raise InvalidRequestError("one file at a time — use 'new_batch' (one table from several files) or 'each' (several independent tables)")
        filename, data = files[0]

        if batch:
            bt = next((b for b in batch_tables if b.name == batch), None)
            if bt is None:
                raise TableNotFoundError(batch)
            target_filename = f"{as_name}{Path(filename).suffix}" if as_name else filename
            target = import_batch_member(root, data, target_filename, bt)
            return {"status": "added", "kind": "batch_member", "batch": batch, "path": str(target)}

        name = as_name or Path(filename).stem
        target_filename = f"{name}{Path(filename).suffix}"
        result = import_single_table(root, data, target_filename, sources, batch_tables, overwrite=overwrite)
        return {"status": "created" if result.created else "updated", "kind": "single", "path": str(result.path)}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def rename_table_route(request: Request) -> JSONResponse:
    """Rename a table end to end (source, sidecar, history, config) —
    see core/table_admin.rename_table. Records an activity event."""
    body = await request.json()
    table_name = body.get("table_name")
    new_name = body.get("new_name")
    if not table_name or not new_name:
        raise InvalidRequestError("missing 'table_name'/'new_name' parameter")
    root = request.app.state.root

    def _run():
        r = rename_table(root, table_name, new_name)
        log_event(root, "table", f"renamed '{r['from']}' → '{r['to']}'")
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def clone_table_route(request: Request) -> JSONResponse:
    """Duplicate a single-file table (source, sidecar, tags/cluster) as
    a new table with fresh history. Records an activity event."""
    body = await request.json()
    table_name = body.get("table_name")
    new_name = body.get("new_name")
    if not table_name or not new_name:
        raise InvalidRequestError("missing 'table_name'/'new_name' parameter")
    root = request.app.state.root

    def _run():
        r = clone_table(root, table_name, new_name)
        log_event(root, "table", f"cloned '{r['from']}' as '{r['to']}'")
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/table/delete", delete_table_route, methods=["POST"]),
    Route("/api/table/rename", rename_table_route, methods=["POST"]),
    Route("/api/table/clone", clone_table_route, methods=["POST"]),
    Route("/api/table/import", import_route, methods=["POST"]),
]
