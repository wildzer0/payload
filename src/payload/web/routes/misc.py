"""view / report / doctor / export / clean — controparte web dei
comandi omonimi in cli.py, stessa suddivisione di responsabilità."""
from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path

import anyio.to_thread
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from payload.core.batch_tables import effective_config
from payload.core.config import load_config
from payload.core.doctor import run_doctor
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve

CLEAN_TARGETS = ("cache", "build", "golden", "all")


async def view(request: Request) -> JSONResponse:
    source = request.query_params.get("source")
    if not source:
        raise InvalidRequestError("parametro 'source' mancante")
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
        rows = []
        for ref in all_table_refs(sources, batch_tables):
            name = ref.name
            table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
            out_dir = resolve(root, table_config.defaults.output_dir)

            pipeline_explicit = bool(table_config.pipeline_stages)
            resolved_reader = resolved_writer = None
            if not pipeline_explicit and not ref.is_batch:
                # resolve_table_outputs qui serve SOLO per il reader/writer
                # risolti da mostrare in dashboard — quali file sono
                # effettivamente su disco resta un fatto puramente fisico
                # (glob), indipendente da cosa la config risolverebbe ORA:
                # un writer ad-hoc (--to) passato a una singola build non è
                # mai nella config, ma il suo output esiste comunque. Non
                # generalizzata a più sorgenti: resta solo per il caso
                # single-file, come già prima delle tabelle batch. Una
                # pipeline esplicita ha già uno shape suo (possibilmente
                # multi-stage/fan-out): mostrare "il reader/writer risolto"
                # come se fosse un caso semplice sarebbe fuorviante, per
                # quello questi due campi restano vuoti in quel caso.
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
            rows.append({
                "name": name,
                "is_batch": ref.is_batch,
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
        raise InvalidRequestError(f"target sconosciuto: '{target}' (cache|build|golden|all)")
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
            return {"status": "noop", "reason": "niente da pulire"}
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
    Route("/api/doctor", doctor_route, methods=["GET"]),
    Route("/api/export", export_route, methods=["GET"]),
    Route("/api/clean", clean_route, methods=["POST"]),
]
