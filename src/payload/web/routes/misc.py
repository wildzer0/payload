"""view / report / doctor / export / clean — web counterpart of the
same-named commands in cli.py, same split of responsibilities."""
from __future__ import annotations

import base64
import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import anyio.to_thread
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from payload.core.clusters import resolve_clusters
from payload.core.config import load_config
from payload.core.discovery import discover_for_history, resolve_table_config, resolve_table_ref
from payload.core.doctor import run_doctor
from payload.core.errors import TableNotFoundError
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.registry import load_plugins
from payload.core.table_meta import resolve_table_meta
from payload.web.errors import InvalidRequestError, NoBuildOutputError
from payload.web.paths import resolve

CLEAN_TARGETS = ("cache", "build", "golden", "all")


async def view(request: Request) -> JSONResponse:
    source = request.query_params.get("source")
    if not source:
        raise InvalidRequestError("missing 'source' parameter")
    reader_name = request.query_params.get("from")
    root = request.app.state.root
    source_path = resolve(root, source)

    def _run():
        registry = load_plugins(project_root=root)
        reader = registry.find_reader(source_path, reader_name)
        ir = reader.parse(source_path, {})
        return {
            "name": ir.name,
            "data_base64": base64.b64encode(ir.data).decode("ascii"),
            "length": len(ir.data),
            "comments": [{"offset": off, "text": text} for off, text in ir.comments],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def report(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        import time

        from payload.core.config import read_raw_sidecar
        from payload.core.discovery import all_table_refs, discover_for_history
        from payload.core.pipeline import resolve_table_outputs

        sources, batch_tables, base_config = discover_for_history(root)
        history = HistoryStore(root)
        registry = load_plugins(strict=False, project_root=root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        rows = []
        for ref in all_table_refs(sources, batch_tables):
            name = ref.name
            table_config = resolve_table_config(root, base_config, ref, clusters, table_metas)
            out_dir = resolve(root, table_config.defaults.output_dir)

            pipeline_explicit = bool(table_config.pipeline_stages)
            resolved_reader = resolved_writer = None
            if not pipeline_explicit and not ref.is_batch:
                # resolve_table_outputs here is ONLY for the resolved
                # reader/writer shown on the dashboard — which files are
                # actually on disk stays a purely physical fact (glob),
                # independent of what the config would resolve to RIGHT
                # NOW: an ad-hoc writer (--to) passed to a single build
                # is never in the config, but its output still exists.
                # Not generalized to multiple sources: stays single-file
                # only, as it already was before batch tables. An
                # explicit pipeline already has its own shape (possibly
                # multi-stage/fan-out): showing "the resolved
                # reader/writer" as if it were a simple case would be
                # misleading, which is why these two fields stay empty
                # in that case.
                _, resolved_reader_name, resolved_writer_names = resolve_table_outputs(
                    ref.source_paths[0], registry, table_config, out_dir
                )
                resolved_reader = resolved_reader_name
                resolved_writer = resolved_writer_names[0] if resolved_writer_names else None

            output_files = list(out_dir.glob(f"{name}.*")) if out_dir.exists() else []
            out_size = output_files[0].stat().st_size if output_files else None
            golden_result = check_golden(history, name, ref.source_paths, output_files)

            last = history.last_snapshot(name)
            has_sidecar = False if ref.is_batch else bool(read_raw_sidecar(ref.source_paths[0]))
            meta = table_metas.get(name)
            rows.append({
                "name": name,
                "is_batch": ref.is_batch,
                "cluster": meta.cluster if meta else None,
                "tags": meta.tags if meta else [],
                "source_count": len(ref.source_paths),
                "source_size": sum(p.stat().st_size for p in ref.source_paths),
                "output_size": out_size,
                "byte_order": table_config.defaults.byte_order,
                "golden_status": golden_result.status,
                "golden_snapshot_id": golden_result.golden_snapshot_id,
                "last_snapshot": {"id": last.id, "timestamp": last.timestamp} if last else None,
                "tip_snapshot_id": history.tip_snapshot_id(name),
                "source_mtime": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(max(p.stat().st_mtime for p in ref.source_paths))
                ),
                "has_sidecar": has_sidecar,
                "pipeline_explicit": pipeline_explicit,
                "reader_override": table_config.defaults.reader,
                "writer_override": table_config.defaults.writer,
                "resolved_reader": resolved_reader,
                "resolved_writer": resolved_writer,
            })
        return {"tables": rows}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def table_download_route(request: Request) -> Response:
    """The CURRENT build output on disk (not a history snapshot: no
    prior commit needed) — a single file is served directly, several
    files (multi-writer fan-out) are zipped on the fly, always and
    only output, never the source: the common case (one writer)
    doesn't pay the cost of a zip for a single file."""
    root = request.app.state.root
    table_name = request.path_params["table_name"]

    def _run() -> list[Path]:
        sources, batch_tables, base_config = discover_for_history(root)
        ref = resolve_table_ref(sources, batch_tables, table_name)
        if ref is None:
            raise TableNotFoundError(table_name)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        table_config = resolve_table_config(root, base_config, ref, clusters, table_metas)
        out_dir = resolve(root, table_config.defaults.output_dir)
        output_files = sorted(out_dir.glob(f"{table_name}.*")) if out_dir.exists() else []
        if not output_files:
            raise NoBuildOutputError(table_name)
        return output_files

    output_files = await anyio.to_thread.run_sync(_run)
    if len(output_files) == 1:
        return FileResponse(output_files[0], filename=output_files[0].name, media_type="application/octet-stream")

    def _zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in output_files:
                zf.writestr(p.name, p.read_bytes())
        return buf.getvalue()

    data = await anyio.to_thread.run_sync(_zip)
    return Response(
        data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{table_name}-output.zip"'},
    )


async def doctor_route(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        registry = load_plugins(strict=False, project_root=root)
        config = load_config(root)
        config_dict = config.model_dump()
        config_dict["_project_root"] = str(root)
        results = run_doctor(config_dict, registry)
        return {
            "checks": [
                {"name": r.name, "status": r.status, "message": r.message, "hint": r.hint}
                for r in results
            ],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def export_route(request: Request):
    root = request.app.state.root
    include_history = request.query_params.get("include_history") == "true"

    def _run():
        from payload.core.discovery import all_table_refs, discover_for_history
        from payload.export import export_project

        sources, batch_tables, _ = discover_for_history(root)
        all_paths = [p for ref in all_table_refs(sources, batch_tables) for p in ref.source_paths]
        tmp_dir = Path(tempfile.mkdtemp(prefix="payload_export_"))
        out_zip = tmp_dir / "export.zip"
        export_project(root, all_paths, out_zip, include_history=include_history)
        return out_zip

    zip_path = await anyio.to_thread.run_sync(_run)
    return FileResponse(
        zip_path, media_type="application/zip", filename="export.zip",
        background=BackgroundTask(shutil.rmtree, zip_path.parent, ignore_errors=True),
    )


async def clean_route(request: Request) -> JSONResponse:
    body = await request.json()
    target = body.get("target", "cache")
    confirm = bool(body.get("confirm", False))
    if target not in CLEAN_TARGETS:
        raise InvalidRequestError(f"unknown target: '{target}' (cache|build|golden|all)")
    root = request.app.state.root

    def _run():
        config = load_config(root)
        dirs = []
        if target in ("cache", "all"):
            dirs.append(resolve(root, config.defaults.cache_dir))
        if target in ("build", "all"):
            dirs.append(resolve(root, config.defaults.output_dir))
        existing = [d for d in dirs if d.exists()]

        history = HistoryStore(root)
        golden_map = history.all_golden() if target in ("golden", "all") else {}

        if not existing and not golden_map:
            return {"status": "noop", "reason": "nothing to clean"}
        if not confirm:
            return {
                "status": "confirmation_required",
                "directories": [str(d) for d in existing],
                "golden_tables": list(golden_map),
            }

        for d in existing:
            shutil.rmtree(d)
        for name in golden_map:
            history.clear_golden(name)
        return {
            "status": "cleaned",
            "directories": [str(d) for d in existing],
            "golden_tables": list(golden_map),
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/view", view, methods=["GET"]),
    Route("/api/report", report, methods=["GET"]),
    Route("/api/table/{table_name}/download", table_download_route, methods=["GET"]),
    Route("/api/doctor", doctor_route, methods=["GET"]),
    Route("/api/export", export_route, methods=["GET"]),
    Route("/api/clean", clean_route, methods=["POST"]),
]
