"""Browser-based project plugin editor: reads/writes a file in
plugins/, live syntax checking (ast.parse, zero new dependencies) and
conformance testing on a single file, even if it isn't loadable yet in
a full PluginRegistry — the same conceptual role as 'pld plugin
validate', but centered on ONE file while it's being written, not on a
plugin that's already registered.

Only the served project's plugins/ is within the editor's scope (not
the additional PAYLOAD_PLUGIN_PATH locations, which can live anywhere
on the filesystem — out of scope for a web editor). Route paths keep
the historical '/api/local-plugins' prefix (internal API surface, not
the user-facing rename)."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.errors import MissingPluginDependenciesError
from payload.core.ir import TableIR
from payload.core.local_plugins import (
    PLUGINS_DIRNAME,
    extract_plugin_classes,
    find_stub_methods,
    load_module_from_file,
    missing_requirements,
    read_requires_static,
)
from payload.testing import (
    check_reader_behavior,
    check_reader_structure,
    check_writer_behavior,
    check_writer_structure,
)
from payload.web.errors import InvalidRequestError, LocalPluginFileNotFoundError
from payload.web.paths import resolve


def _safe_filename(raw: str) -> str:
    """A plain filename inside plugins/, never a path — no
    separators, no '..': the web editor must not be able to read or
    write files outside that folder."""
    if not raw.endswith(".py") or "/" in raw or "\\" in raw or raw in (".", "..") or raw.startswith("."):
        raise InvalidRequestError(f"invalid filename: '{raw}'")
    return raw


def _local_plugins_dir(root: Path) -> Path:
    return root / PLUGINS_DIRNAME


async def local_plugins_list(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        d = _local_plugins_dir(root)
        items = []
        for f in sorted(d.glob("*.py")) if d.is_dir() else []:
            if f.name.startswith("_"):
                continue
            kinds: list[str] = []
            try:
                module = load_module_from_file(f)
                kinds = sorted({kind for kind, _ in extract_plugin_classes(module)})
            except Exception:
                pass  # a not-yet-valid file is still listable/openable in the editor
            items.append({
                "filename": f.name, "size": f.stat().st_size, "kinds": kinds,
                "stub_methods": find_stub_methods(f),
            })
        return {"files": items}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def local_plugin_get(request: Request) -> JSONResponse:
    filename = _safe_filename(request.path_params["filename"])
    root = request.app.state.root

    def _run():
        path = _local_plugins_dir(root) / filename
        if not path.is_file():
            raise LocalPluginFileNotFoundError(filename)
        return {"filename": filename, "content": path.read_text(encoding="utf-8")}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def local_plugin_put(request: Request) -> JSONResponse:
    filename = _safe_filename(request.path_params["filename"])
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise InvalidRequestError("missing 'content' parameter")
    root = request.app.state.root

    def _run():
        d = _local_plugins_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")
        return {"filename": filename, "saved": True}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def local_plugin_delete(request: Request) -> JSONResponse:
    filename = _safe_filename(request.path_params["filename"])
    root = request.app.state.root

    def _run():
        path = _local_plugins_dir(root) / filename
        existed = path.is_file()
        if existed:
            path.unlink()
        return {"filename": filename, "status": "deleted" if existed else "not_found"}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def local_plugin_syntax_check(request: Request) -> JSONResponse:
    body = await request.json()
    content = body.get("content", "")

    def _run():
        try:
            ast.parse(content)
            return {"valid": True}
        except SyntaxError as e:
            return {"valid": False, "line": e.lineno, "column": e.offset, "message": e.msg}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


def _structural_result(kind: str, instance) -> dict:
    name = getattr(instance, "name", "?")
    if kind == "reader":
        issues = check_reader_structure(instance)
    elif kind == "writer":
        issues = check_writer_structure(instance)
    else:
        issues = []
        if not hasattr(instance, "name"):
            issues.append({"check": "attributes", "detail": "missing the 'name' attribute"})
        if not callable(getattr(instance, "run", None)):
            issues.append({"check": "attributes", "detail": "missing a callable 'run(config)' method"})
        return {"kind": kind, "name": name, "loadable": True, "conforms": not issues, "skipped_behavior_check": True, "issues": issues}
    return {"kind": kind, "name": name, "loadable": True, "conforms": not issues, "issues": [{"check": i.check, "detail": i.detail} for i in issues]}


async def local_plugin_test(request: Request) -> JSONResponse:
    filename = _safe_filename(request.path_params["filename"])
    body = await request.json()
    sample = body.get("sample")
    root = request.app.state.root

    def _run():
        path = _local_plugins_dir(root) / filename
        if not path.is_file():
            raise LocalPluginFileNotFoundError(filename)

        requires = read_requires_static(path)
        if requires:
            missing = missing_requirements(requires)
            if missing:
                raise MissingPluginDependenciesError(path, missing)

        try:
            module = load_module_from_file(path)
        except Exception as e:
            return {"loadable": False, "error": f"{type(e).__name__}: {e}"}

        classes = extract_plugin_classes(module)
        if not classes:
            return {"loadable": False, "error": "no READER/WRITER/DOCTOR_CHECK declared in the file"}

        results = []
        for kind, cls in classes:
            try:
                instance = cls()
            except Exception as e:
                results.append({"kind": kind, "name": getattr(cls, "name", cls.__name__), "loadable": False, "error": f"{type(e).__name__}: {e}"})
                continue

            if kind == "reader":
                result = _structural_result(kind, instance)
                skipped = sample is None
                if not skipped:
                    behavior_issues = check_reader_behavior(instance, resolve(root, sample))
                    result["issues"] += [{"check": i.check, "detail": i.detail} for i in behavior_issues]
                    result["conforms"] = not result["issues"]
                result["skipped_behavior_check"] = skipped
                results.append(result)
            elif kind == "writer":
                result = _structural_result(kind, instance)
                sample_ir = TableIR(name="conformance_sample", data=b"\x00\x01\x02", source_path=Path("conformance_sample"), source_format="testing")
                with tempfile.TemporaryDirectory() as tmp:
                    behavior_issues = check_writer_behavior(instance, sample_ir, Path(tmp))
                result["issues"] += [{"check": i.check, "detail": i.detail} for i in behavior_issues]
                result["conforms"] = not result["issues"]
                result["skipped_behavior_check"] = False
                results.append(result)
            else:
                results.append(_structural_result(kind, instance))

        return {"loadable": True, "results": results}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/local-plugins", local_plugins_list, methods=["GET"]),
    Route("/api/local-plugins/syntax-check", local_plugin_syntax_check, methods=["POST"]),
    Route("/api/local-plugins/{filename}", local_plugin_get, methods=["GET"]),
    Route("/api/local-plugins/{filename}", local_plugin_put, methods=["PUT"]),
    Route("/api/local-plugins/{filename}", local_plugin_delete, methods=["DELETE"]),
    Route("/api/local-plugins/{filename}/test", local_plugin_test, methods=["POST"]),
]
