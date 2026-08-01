"""Editor dei plugin locali dal browser: lettura/scrittura di un file
in local_plugins/, controllo sintassi live (ast.parse, zero dipendenze
nuove) e test di conformità su un singolo file, anche se non è ancora
caricabile in un PluginRegistry completo — stesso ruolo concettuale di
'pld plugin validate', ma centrato su UN file mentre lo si scrive,
non su un plugin già registrato.

Solo local_plugins/ del progetto servito è nel dominio dell'editor
(non i percorsi aggiuntivi di PAYLOAD_PLUGIN_PATH, che possono vivere
ovunque sul filesystem — fuori scope per un editor web)."""
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
    LOCAL_PLUGINS_DIRNAME,
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
    """Un filename semplice dentro local_plugins/, mai un path — niente
    separatori, niente '..': l'editor web non deve poter leggere o
    scrivere file fuori da quella cartella."""
    if not raw.endswith(".py") or "/" in raw or "\\" in raw or raw in (".", "..") or raw.startswith("."):
        raise InvalidRequestError(f"nome file non valido: '{raw}'")
    return raw


def _local_plugins_dir(root: Path) -> Path:
    return root / LOCAL_PLUGINS_DIRNAME


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
                pass  # un file non ancora valido resta comunque elencabile/apribile nell'editor
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
        raise InvalidRequestError("parametro 'content' mancante")
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
            issues.append({"check": "attributi", "detail": "manca l'attributo 'name'"})
        if not callable(getattr(instance, "run", None)):
            issues.append({"check": "attributi", "detail": "manca un metodo 'run(config)' chiamabile"})
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
            return {"loadable": False, "error": "nessun READER/WRITER/DOCTOR_CHECK dichiarato nel file"}

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
