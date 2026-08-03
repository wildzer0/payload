"""config show/edit, per-table sidecar, pipeline show/edit — web
counterpart of the same-named commands in cli.py plus the write
operations the CLI doesn't expose yet (global/sidecar config editing,
visual pipeline builder): all write through payload.core.config, which
validates BEFORE touching disk."""
from __future__ import annotations

from dataclasses import fields as dc_fields
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.cache import BuildCache, compute_pipeline_cache_key, compute_pipeline_cache_key_multi
from payload.core.clusters import resolve_clusters
from payload.core.config import (
    config_schema,
    delete_sidecar_config,
    read_raw_sidecar,
    resolve_config_with_provenance,
    upsert_batch_table,
    write_global_config,
    write_sidecar_config,
)
from payload.core.discovery import discover_for_history, resolve_table_config, resolve_table_ref
from payload.core.errors import TableNotFoundError
from payload.core.pipeline import final_output_paths, resolve_pipeline_spec, validate_pipeline_against_registry
from payload.core.pipeline_spec import ExecStage, PipelineSpec, ReaderStage, Stage, WriterStage
from payload.core.registry import load_plugins
from payload.core.table_meta import resolve_table_meta
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


def _find_ref(sources: list[Path], batch_tables: list, table_name: str):
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        raise TableNotFoundError(table_name)
    return ref


def _reject_batch(ref) -> None:
    if ref.is_batch:
        raise InvalidRequestError(
            f"'{ref.name}' is a batch table: it has no sidecar, overrides "
            "live inline in [[batch_table]] (see src/payload/docs/BATCH.md)"
        )


def _config_payload(root: Path, table: str | None) -> dict:
    source_path = None
    if table:
        sources, batch_tables, _ = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        # a batch table has no source_path to resolve a sidecar from
        # (its overrides live inline in [[batch_table]]) — show only
        # global+cluster in that case, not the (unsupported here)
        # sidecar tier.
        if not ref.is_batch:
            source_path = ref.source_paths[0]

    config, provenance = resolve_config_with_provenance(root, source_path=source_path, table_name=table)

    fields = []
    for section_name, section_obj in (("defaults", config.defaults),):
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
    defaults = body.get("defaults")
    if not isinstance(defaults, dict):
        raise InvalidRequestError("missing or invalid 'defaults' parameter (expected a table)")
    root = request.app.state.root

    def _run():
        write_global_config(root, defaults)
        return _config_payload(root, None)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_get_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        _reject_batch(ref)
        return read_raw_sidecar(ref.source_paths[0])

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_put_route(request: Request) -> JSONResponse:
    body = await request.json()
    defaults = body.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise InvalidRequestError("'defaults' must be a table")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        _reject_batch(ref)
        write_sidecar_config(ref.source_paths[0], defaults=defaults)
        return read_raw_sidecar(ref.source_paths[0])

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def sidecar_delete_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, _ = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        _reject_batch(ref)
        deleted = delete_sidecar_config(ref.source_paths[0])
        return {"status": "deleted" if deleted else "not_found"}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


def _stage_to_raw(stage: Stage) -> dict:
    raw = {}
    if isinstance(stage, ReaderStage):
        raw.update({"type": "reader", "name": stage.name})
    elif isinstance(stage, WriterStage):
        raw.update({"type": "writer", "name": stage.name})
    else:
        raw.update({"type": "exec", "command": stage.command, "on_error": stage.on_error})
        if stage.output_extension:
            raw["output_extension"] = stage.output_extension
    # presentational canvas position (kept out of the cache signature)
    if stage.x is not None:
        raw["x"] = stage.x
    if stage.y is not None:
        raw["y"] = stage.y
    return raw


def _pipeline_payload(root: Path, ref, base_config, table: str) -> dict:
    registry = load_plugins(project_root=root)
    clusters = resolve_clusters(root, base_config)
    table_metas = resolve_table_meta(root, base_config, clusters)
    config = resolve_table_config(root, base_config, ref, clusters, table_metas)

    spec = resolve_pipeline_spec(ref.source_paths[0], registry, config, None, None)
    validate_pipeline_against_registry(spec, registry)

    cache = BuildCache(resolve(root, config.defaults.cache_dir))
    config_dict = config.model_dump()
    if ref.is_batch:
        named_sources = sorted((p.name, p.read_bytes()) for p in ref.source_paths)
    else:
        source_bytes = ref.source_paths[0].read_bytes()
    terminal_start = spec.terminal_writer_start()

    stages = []
    for i, stage in enumerate(spec.stages):
        if isinstance(stage, ReaderStage):
            entry = {"index": i, "kind": "reader", "name": stage.name}
        elif isinstance(stage, WriterStage):
            entry = {"index": i, "kind": "writer", "name": stage.name}
        else:
            entry = {"index": i, "kind": "exec", "command": stage.command, "on_error": stage.on_error,
                     "output_extension": getattr(stage, "output_extension", None) or ""}
        if stage.x is not None:
            entry["x"] = stage.x
        if stage.y is not None:
            entry["y"] = stage.y

        if isinstance(stage, (WriterStage, ExecStage)):
            if i >= terminal_start:
                checkpoint = "final"
            else:
                if ref.is_batch:
                    checkpoint_key = compute_pipeline_cache_key_multi(named_sources, spec.signature_prefix(i), config_dict)
                else:
                    checkpoint_key = compute_pipeline_cache_key(source_bytes, spec.signature_prefix(i), config_dict)
                checkpoint = "valid" if cache.is_fresh(f"{table}::stage{i}", checkpoint_key) else "none"
            entry["checkpoint"] = checkpoint
        stages.append(entry)

    out_paths = final_output_paths(spec, table, resolve(root, config.defaults.output_dir), registry)
    return {
        "table": table, "stages": stages, "outputs": [str(p) for p in out_paths],
        "explicit": bool(config.pipeline_stages),
    }


async def pipeline_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        return _pipeline_payload(root, ref, config, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def pipeline_put_route(request: Request) -> JSONResponse:
    body = await request.json()
    raw_stages = body.get("stages")
    if not isinstance(raw_stages, list):
        raise InvalidRequestError("missing 'stages' parameter or it isn't a list")
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)

        registry = load_plugins(project_root=root)
        spec = PipelineSpec.from_raw_stages(raw_stages)
        validate_pipeline_against_registry(spec, registry)

        serialized = [_stage_to_raw(s) for s in spec.stages]
        if ref.is_batch:
            # a batch has no sidecar: its pipeline lives inline in the
            # [[batch_table]] entry (the 'stages' field), exactly like
            # the reader/writer/byte_order overrides
            entry = next((e for e in config.batch_tables if e.get("name") == table), None)
            upsert_batch_table(root, table, list(entry["sources"]) if entry else [], stages=serialized)
        else:
            write_sidecar_config(ref.source_paths[0], pipeline_stages=serialized)
        # re-discover: the payload must reflect the just-written stages
        sources, batch_tables, config = discover_for_history(root)
        return _pipeline_payload(root, _find_ref(sources, batch_tables, table), config, table)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def pipeline_delete_route(request: Request) -> JSONResponse:
    root = request.app.state.root
    table = request.path_params["table_name"]

    def _run():
        sources, batch_tables, config = discover_for_history(root)
        ref = _find_ref(sources, batch_tables, table)
        if ref.is_batch:
            entry = next((e for e in config.batch_tables if e.get("name") == table), None)
            upsert_batch_table(root, table, list(entry["sources"]) if entry else [], stages=[])
        else:
            write_sidecar_config(ref.source_paths[0], pipeline_stages=[])
        sources, batch_tables, config = discover_for_history(root)
        return _pipeline_payload(root, _find_ref(sources, batch_tables, table), config, table)

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
