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
from payload.core.discovery import describe_problems, discover_for_history, discover_for_history_lenient, resolve_table_config, resolve_table_ref
from payload.core.doctor import run_doctor
from payload.core.errors import TableNotFoundError
from payload.core.file_ops import analyze_file
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

    def _int_param(name: str, default: int) -> int:
        raw = request.query_params.get(name)
        if not raw:
            return default
        try:
            return max(0, int(raw))
        except ValueError:
            return default

    offset = _int_param("offset", 0)
    limit = _int_param("limit", 0)  # 0 = whole file (backward compatible)

    def _run():
        registry = load_plugins(project_root=root)
        reader = registry.find_reader(source_path, reader_name)
        ir = reader.parse(source_path, {})
        data = ir.data
        end = len(data) if limit <= 0 else min(offset + limit, len(data))
        chunk = data[offset:end]
        return {
            "name": ir.name,
            "data_base64": base64.b64encode(chunk).decode("ascii"),
            "length": len(ir.data),
            "offset": offset,
            "limit": limit,
            "has_more": end < len(data),
            # comments keep absolute (file) offsets; the slice only
            # filters which ones are relevant to the current page
            "comments": [
                {"offset": off, "text": text}
                for off, text in ir.comments
                if offset <= off < end
            ],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def report(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        from payload.core.discovery import describe_problems
        from payload.core.report import collect_report_data

        data = collect_report_data(root)
        data["warnings"] = describe_problems(data.pop("problems"))
        return data

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def report_html_route(request: Request) -> Response:
    """The printable HTML report of the whole project (same content as
    the CLI 'pld report') — opened in a new tab, printed with the
    browser's 'Save as PDF' (zero-dependency choice)."""
    root = request.app.state.root

    def _run():
        from payload.core.report import render_report_html

        return render_report_html(root)

    return Response(
        await anyio.to_thread.run_sync(_run),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="payload-report.html"'},
    )


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


async def table_analyze_route(request: Request) -> JSONResponse:
    """Analyze (entropy/magic/frequency) the table's latest built output —
    the table-page counterpart of the Files page's Analyze."""
    root = request.app.state.root
    table_name = request.path_params["table_name"]

    def _run():
        sources, batch_tables, base_config = discover_for_history(root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        ref = resolve_table_ref(sources, batch_tables, table_name)
        if ref is None:
            raise TableNotFoundError(table_name)
        table_config = resolve_table_config(root, base_config, ref, clusters, table_metas)
        out_dir = resolve(root, table_config.defaults.output_dir)
        outputs = sorted(out_dir.glob(f"{ref.name}.*")) if out_dir.exists() else []
        if not outputs:
            raise NoBuildOutputError(table_name)
        r = analyze_file(outputs[0])
        r["path"] = str(outputs[0])
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/view", view, methods=["GET"]),
    Route("/api/report", report, methods=["GET"]),
    Route("/api/report/html", report_html_route, methods=["GET"]),
    Route("/api/table/{table_name}/download", table_download_route, methods=["GET"]),
    Route("/api/table/{table_name}/analyze", table_analyze_route, methods=["GET"]),
    Route("/api/doctor", doctor_route, methods=["GET"]),
    Route("/api/export", export_route, methods=["GET"]),
    Route("/api/clean", clean_route, methods=["POST"]),
]
