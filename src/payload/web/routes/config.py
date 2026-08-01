"""config show/edit, sidecar per-tabella, pipeline show/edit — controparte
web dei comandi omonimi in cli.py più le operazioni di scrittura che la
CLI non espone ancora (editing config globale/sidecar, pipeline builder
visuale): tutte scrivono tramite payload.core.config, che valida PRIMA
di toccare il disco."""
from __future__ import annotations

from dataclasses import fields as dc_fields
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.cache import BuildCache, compute_pipeline_cache_key
from payload.core.config import (
    config_schema,
    delete_sidecar_config,
    load_config,
    read_raw_sidecar,
    resolve_config_with_provenance,
    write_global_config,
    write_sidecar_config,
)
from payload.core.discovery import discover_for_history
from payload.core.errors import TableNotFoundError
from payload.core.pipeline import final_output_paths, resolve_pipeline_spec, validate_pipeline_against_registry
from payload.core.pipeline_spec import ExecStage, PipelineSpec, ReaderStage, Stage, WriterStage
from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


def _find_source(sources: list[Path], table_name: str) -> Path:
    src = next((s for s in sources if s.stem == table_name), None)
    if src is None:
        raise TableNotFoundError(table_name)
    return src


def _config_payload(root: Path, table: str | None) -> dict:
    source_path = None
    if table:
        sources, _ = discover_for_history(root)
        source_path = _find_source(sources, table)

    config, provenance = resolve_config_with_provenance(root, source_path=source_path)

    fields = []
    for section_name, section_obj in (("defaults", config.defaults), ("toolchain", config.toolchain)):
        for f in dc_fields(section_obj):
            key = f"{section_name}.{f.name}"
            fields.append({
                "key": key, "value": getattr(section_obj, f.name),
                "origin": provenance.get(key, "default"),
            })

    for key, origin in sorted(provenance.items()):
        if key.startswith("plugin."):
            value = config.plugin
            for part in key.split(".")[1:]:
                value = value.get(part, {}) if isinstance(value, dict) else value
            fields.append({"key": key, "value": value, "origin": origin})

    return {"table": table, "fields": fields, "plugin": config.plugin, "schema": config_schema()}


async def config_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.query_params.get("table")

    def _run():
        return _config_payload(root, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def config_put_route(request: Request) -> JSONResponse:
    body = await request.json()
    defaults, toolchain = body.get("defaults"), body.get("toolchain")
    if not isinstance(defaults, dict) or not isinstance(toolchain, dict):
        raise InvalidRequestError("parametri 'defaults'/'toolchain' mancanti o non validi (attese due tabelle)")
    root = request.app.state.root

    def _run():
        write_global_config(root, defaults, toolchain)
        return _config_payload(root, None)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_get_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        return read_raw_sidecar(src)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_put_route(request: Request) -> JSONResponse:
    body = await request.json()
    defaults, toolchain = body.get("defaults"), body.get("toolchain")
    if defaults is not None and not isinstance(defaults, dict):
        raise InvalidRequestError("'defaults' deve essere una tabella")
    if toolchain is not None and not isinstance(toolchain, dict):
        raise InvalidRequestError("'toolchain' deve essere una tabella")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        write_sidecar_config(src, defaults=defaults, toolchain=toolchain)
        return read_raw_sidecar(src)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_delete_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        deleted = delete_sidecar_config(src)
        return {"status": "deleted" if deleted else "not_found"}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


def _stage_to_raw(stage: Stage) -> dict:
    if isinstance(stage, ReaderStage):
        return {"type": "reader", "name": stage.name}
    if isinstance(stage, WriterStage):
        return {"type": "writer", "name": stage.name}
    raw = {"type": "exec", "command": stage.command, "on_error": stage.on_error}
    if stage.output_extension:
        raw["output_extension"] = stage.output_extension
    return raw


def _pipeline_payload(root: Path, src: Path, table: str) -> dict:
    registry = load_plugins(project_root=root)
    config = load_config(root, source_path=src)

    spec = resolve_pipeline_spec(src, registry, config, None, None)
    validate_pipeline_against_registry(spec, registry)

    cache = BuildCache(resolve(root, config.defaults.cache_dir))
    source_bytes = src.read_bytes()
    config_dict = config.model_dump()
    table_key = str(src)
    terminal_start = spec.terminal_writer_start()

    stages = []
    for i, stage in enumerate(spec.stages):
        if isinstance(stage, ReaderStage):
            entry = {"index": i, "kind": "reader", "name": stage.name}
        elif isinstance(stage, WriterStage):
            entry = {"index": i, "kind": "writer", "name": stage.name}
        else:
            entry = {"index": i, "kind": "exec", "command": stage.command, "on_error": stage.on_error}

        if isinstance(stage, (WriterStage, ExecStage)):
            if i >= terminal_start:
                checkpoint = "final"
            else:
                checkpoint_key = compute_pipeline_cache_key(source_bytes, spec.signature_prefix(i), config_dict)
                checkpoint = "valid" if cache.is_fresh(f"{table_key}::stage{i}", checkpoint_key) else "none"
            entry["checkpoint"] = checkpoint
        stages.append(entry)

    out_paths = final_output_paths(spec, src, resolve(root, config.defaults.output_dir), registry)
    return {
        "table": table, "stages": stages, "outputs": [str(p) for p in out_paths],
        "explicit": bool(config.pipeline_stages),
    }


async def pipeline_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        return _pipeline_payload(root, src, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def pipeline_put_route(request: Request) -> JSONResponse:
    body = await request.json()
    raw_stages = body.get("stages")
    if not isinstance(raw_stages, list):
        raise InvalidRequestError("parametro 'stages' mancante o non è una lista")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)

        registry = load_plugins(project_root=root)
        spec = PipelineSpec.from_raw_stages(raw_stages)
        validate_pipeline_against_registry(spec, registry)

        write_sidecar_config(src, pipeline_stages=[_stage_to_raw(s) for s in spec.stages])
        return _pipeline_payload(root, src, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def pipeline_delete_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, _ = discover_for_history(root)
        src = _find_source(sources, table)
        write_sidecar_config(src, pipeline_stages=[])
        return _pipeline_payload(root, src, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/config", config_route, methods=["GET"]),
    Route("/api/config", config_put_route, methods=["PUT"]),
    Route("/api/sidecar/{table_name}", sidecar_get_route, methods=["GET"]),
    Route("/api/sidecar/{table_name}", sidecar_put_route, methods=["PUT"]),
    Route("/api/sidecar/{table_name}", sidecar_delete_route, methods=["DELETE"]),
    Route("/api/pipeline/{table_name}", pipeline_route, methods=["GET"]),
    Route("/api/pipeline/{table_name}", pipeline_put_route, methods=["PUT"]),
    Route("/api/pipeline/{table_name}", pipeline_delete_route, methods=["DELETE"]),
]
