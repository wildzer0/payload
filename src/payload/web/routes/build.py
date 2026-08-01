"""build (singola tabella) — controparte web di 'pld build' in cli.py.
'GET /api/build-all/stream' è la controparte web di 'pld build-all',
via Server-Sent Events invece che una tabella rich statica: la usa
un GET (non POST) perché l'EventSource nativo del browser sa fare
solo GET, niente body/header custom."""
from __future__ import annotations

import json
import logging
import queue
import threading

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from payload.core.batch import run_batch_build
from payload.core.cache import BuildCache
from payload.core.config import load_config
from payload.core.discovery import discover_table_sources, find_duplicate_stems
from payload.core.errors import DuplicateTableNameError, GoldenMismatchError, GoldenStaleError, PayloadError
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.pipeline import build
from payload.core.registry import load_plugins
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve
from payload.web.sse import sse_format

# ThreadPoolExecutor(max_workers=jobs) non ha un tetto proprio — un
# valore assurdo (es. digitato per errore, o passato di proposito) può
# far esplodere la creazione di thread. Stesso numero anche lato JS
# (MAX_BUILD_ALL_JOBS in app.js) per il tetto del campo numerico, ma
# quello è solo un aiuto per l'utente: questo è il controllo che conta
# davvero, un client non deve poter bypassarlo.
MAX_BUILD_ALL_JOBS = 32


async def build_route(request: Request) -> JSONResponse:
    body = await request.json()
    source = body.get("source")
    if not source:
        raise InvalidRequestError("parametro 'source' mancante")
    root = request.app.state.root
    source_path = resolve(root, source)
    out_dir = resolve(root, body.get("out") or "build")

    def _run():
        registry = load_plugins(project_root=root)
        config = load_config(root, source_path=source_path)
        cache = BuildCache(resolve(root, config.defaults.cache_dir))

        out_paths, was_built = build(
            source_path, registry, config, out_dir, cache=cache,
            reader_name=body.get("from"), writer_name=body.get("to"),
            force=bool(body.get("force", False)), dry_run=bool(body.get("dry_run", False)),
            cli_opts=body.get("opt") or None, keep_intermediate=bool(body.get("keep_intermediate", False)),
        )
        cache.save()

        golden_status = None
        if body.get("check_golden") and not body.get("dry_run"):
            history = HistoryStore(root)
            result = check_golden(history, source_path.stem, source_path, out_paths)
            golden_status = result.status
            if result.status == "mismatch":
                raise GoldenMismatchError(source_path.stem)
            if result.status == "stale":
                raise GoldenStaleError(source_path.stem)

        return {
            "outputs": [str(p) for p in out_paths],
            "was_built": was_built,
            "golden_status": golden_status,
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


def _summary_to_dict(summary) -> dict:
    return {
        "built": summary.built,
        "cached": summary.cached,
        "golden_mismatch": summary.golden_mismatch,
        "errors": summary.errors,
        "failures": [f.to_dict() for f in summary.failures],
    }


async def build_all_stream(request: Request) -> StreamingResponse:
    root = request.app.state.root
    params = request.query_params
    to = params.get("to")
    out_dir = resolve(root, params.get("out") or "build")
    try:
        jobs = int(params.get("jobs") or 1)
    except ValueError:
        raise InvalidRequestError("parametro 'jobs' deve essere un numero intero")
    if not (1 <= jobs <= MAX_BUILD_ALL_JOBS):
        raise InvalidRequestError(f"parametro 'jobs' deve essere tra 1 e {MAX_BUILD_ALL_JOBS}")
    filter_glob = params.get("filter")
    force = params.get("force") == "true"
    dry_run = params.get("dry_run") == "true"
    check_golden_flag = params.get("check_golden") == "true"

    async def event_gen():
        q: queue.Queue = queue.Queue()
        DONE = object()

        def on_table_result(src, status):
            q.put(("progress", {"source": str(src), "status": status}))

        def worker():
            try:
                registry = load_plugins(project_root=root)
                base_config = load_config(root)
                cache = BuildCache(resolve(root, base_config.defaults.cache_dir))
                known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
                sources = discover_table_sources(root, known_ext, resolve(root, base_config.defaults.output_dir), filter_glob)

                duplicates = find_duplicate_stems(sources)
                if duplicates:
                    raise DuplicateTableNameError(duplicates)

                summary = run_batch_build(
                    sources, root, registry, cache, out_dir, jobs=jobs, writer_name=to,
                    force=force, dry_run=dry_run, check_golden_flag=check_golden_flag,
                    on_table_result=on_table_result,
                )
                q.put(("summary", _summary_to_dict(summary)))
            except PayloadError as e:
                q.put(("error", e.to_dict()))
            except Exception:
                logging.getLogger("payload.web").exception("build-all: errore interno")
                q.put(("error", {"error": "InternalError", "message": "Errore interno inatteso"}))
            finally:
                q.put((DONE, None))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, payload = await anyio.to_thread.run_sync(q.get)
            if kind is DONE:
                break
            yield sse_format(kind, json.dumps(payload))

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


ROUTES = [
    Route("/api/build", build_route, methods=["POST"]),
    Route("/api/build-all/stream", build_all_stream, methods=["GET"]),
]
