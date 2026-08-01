"""plugins / plugin info / plugin validate / plugin new-local / plugin
new — controparte web dei comandi omonimi in cli.py, stessa
suddivisione di responsabilità."""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.errors import PluginNotFoundError, ToolchainExecutionError
from payload.core.ir import TableIR
from payload.core.registry import load_plugins
from payload.plugin_scaffold import scaffold_local_plugin, scaffold_plugin
from payload.testing import (
    check_reader_behavior,
    check_reader_structure,
    check_writer_behavior,
    check_writer_structure,
)
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve


async def plugins_list(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        registry = load_plugins(strict=False, project_root=root)
        items = []
        for kind, group in (
            ("reader", registry.readers), ("writer", registry.writers), ("doctor_check", registry.doctor_checks),
        ):
            for name, plugin in group.items():
                ext = getattr(plugin, "extensions", None) or [getattr(plugin, "extension", "")]
                items.append({
                    "kind": kind, "name": name,
                    "extensions": [e for e in ext if e],
                    "api_version": getattr(plugin, "api_version", "?"),
                    "builtin": registry.is_builtin(name),
                })
        return {"plugins": items}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def plugin_info(request: Request) -> JSONResponse:
    root = request.app.state.root
    name = request.path_params["name"]

    def _run():
        registry = load_plugins(strict=False, project_root=root)
        plugin = registry.readers.get(name) or registry.writers.get(name) or registry.doctor_checks.get(name)
        if plugin is None:
            raise PluginNotFoundError(name)

        kind = "reader" if name in registry.readers else "writer" if name in registry.writers else "doctor_check"
        doc = (inspect.getdoc(plugin) or "").strip()
        return {
            "name": name,
            "kind": kind,
            "api_version": getattr(plugin, "api_version", "?"),
            "extensions": getattr(plugin, "extensions", None),
            "extension": getattr(plugin, "extension", None),
            "default_writer": getattr(plugin, "default_writer", None),
            "compatible_readers": getattr(plugin, "compatible_readers", None),
            "docstring": doc or None,
            "builtin": registry.is_builtin(name),
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def plugin_validate(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    if not name:
        raise InvalidRequestError("parametro 'name' mancante")
    sample = body.get("sample")
    root = request.app.state.root

    def _run():
        registry = load_plugins(strict=False, project_root=root)
        issues = []

        if name in registry.readers:
            reader = registry.readers[name]
            issues += check_reader_structure(reader)
            if sample is None:
                skipped_behavior = True
            else:
                skipped_behavior = False
                issues += check_reader_behavior(reader, resolve(root, sample))
        elif name in registry.writers:
            writer = registry.writers[name]
            skipped_behavior = False
            issues += check_writer_structure(writer)
            sample_ir = TableIR(
                name="conformance_sample", data=b"\x00\x01\x02",
                source_path=Path("conformance_sample"), source_format="testing",
            )
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                issues += check_writer_behavior(writer, sample_ir, Path(tmp))
        else:
            raise PluginNotFoundError(name)

        return {
            "conforms": not issues,
            "skipped_behavior_check": skipped_behavior,
            "issues": [{"check": i.check, "detail": i.detail} for i in issues],
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def plugin_install_deps_route(request: Request) -> JSONResponse:
    body = await request.json()
    file = body.get("file")
    if not file:
        raise InvalidRequestError("parametro 'file' mancante")
    confirm = bool(body.get("confirm", False))
    file_path = resolve(request.app.state.root, file)

    def _run():
        from payload.core.local_plugins import missing_requirements, read_requires_static

        requires = read_requires_static(file_path)
        if not requires:
            return {"status": "noop", "reason": "nessun REQUIRES dichiarato"}

        missing = missing_requirements(requires)
        if not missing:
            return {"status": "noop", "reason": "già installate"}

        if not confirm:
            return {"status": "confirmation_required", "missing": missing}

        cmd = [sys.executable, "-m", "pip", "install", *missing]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ToolchainExecutionError(cmd, result.returncode, stderr=result.stderr)
        return {"status": "installed", "installed": missing}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def plugin_new_local_route(request: Request) -> JSONResponse:
    body = await request.json()
    name, kind = body.get("name"), body.get("kind")
    if not name or not kind:
        raise InvalidRequestError("parametri 'name'/'kind' mancanti")
    root = request.app.state.root
    dest = resolve(root, body.get("dest") or "local_plugins")

    def _run():
        try:
            out_path = scaffold_local_plugin(name, kind, dest)
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        except FileExistsError as e:
            raise InvalidRequestError(str(e)) from e
        return {"created": str(out_path)}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def plugin_new_route(request: Request) -> JSONResponse:
    body = await request.json()
    name, kind = body.get("name"), body.get("kind")
    if not name or not kind:
        raise InvalidRequestError("parametri 'name'/'kind' mancanti")
    root = request.app.state.root
    dest = resolve(root, body.get("dest") or ".")

    def _run():
        try:
            out_dir = scaffold_plugin(name, kind, dest)
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        return {"created": str(out_dir)}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/plugins", plugins_list, methods=["GET"]),
    Route("/api/plugin/{name}", plugin_info, methods=["GET"]),
    Route("/api/plugin/validate", plugin_validate, methods=["POST"]),
    Route("/api/plugin/install-deps", plugin_install_deps_route, methods=["POST"]),
    Route("/api/plugin/new-local", plugin_new_local_route, methods=["POST"]),
    Route("/api/plugin/new", plugin_new_route, methods=["POST"]),
]
