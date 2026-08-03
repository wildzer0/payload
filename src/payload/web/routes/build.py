"""build (single table) — web counterpart of 'pld build' in cli.py.
'GET /api/build-all/stream' is the web counterpart of 'pld build-all',
via Server-Sent Events instead of a static rich table: it uses GET
(not POST) because the browser's native EventSource can only do GET,
no custom body/headers."""
from __future__ import annotations

import base64
import json
import logging
import queue
import threading

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from payload.core.batch import run_batch_build
from payload.core.batch_tables import effective_config, resolve_batch_tables
from payload.core.activity import log_event
from payload.core.cache import BuildCache
from payload.core.clusters import resolve_clusters
from payload.core.config import load_config
from payload.core.discovery import (
    all_table_refs,
    check_no_batch_name_collisions,
    discover_table_sources,
    exclude_batch_members,
    find_duplicate_stems,
)
from payload.core.errors import (
    ClusterError,
    DuplicateTableNameError,
    GoldenMismatchError,
    GoldenStaleError,
    PayloadError,
    SourceNotFoundError,
)
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.pipeline import build
from payload.core.registry import load_plugins
from payload.core.table_meta import resolve_table_meta
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve
from payload.web.sse import sse_format

# ThreadPoolExecutor(max_workers=jobs) has no cap of its own — an
# absurd value (e.g. typed by mistake, or passed on purpose) could
# blow up thread creation. Same number also on the JS side
# (MAX_BUILD_ALL_JOBS in js/views/build_all.js) for the numeric
# field's cap, but that's just a courtesy for the user: this is the
# check that actually matters, a client must not be able to bypass it.
MAX_BUILD_ALL_JOBS = 32


async def build_route(request: Request) -> JSONResponse:
    body = await request.json()
    source = body.get("source")
    if not source:
        raise InvalidRequestError("missing 'source' parameter")
    root = request.app.state.root
    out_dir = resolve(root, body.get("out") or "build")

    def _run():
        registry = load_plugins(project_root=root)
        source_path = resolve(root, source)

        if source_path.is_file():
            source_paths = [source_path]
            config = load_config(root, source_path=source_path)
            table_name = source_path.stem
        else:
            base_config = load_config(root)
            batch = next((b for b in resolve_batch_tables(root, base_config) if b.name == source), None)
            if batch is None:
                raise SourceNotFoundError(source_path)
            source_paths = batch.source_paths
            clusters = resolve_clusters(root, base_config)
            table_metas = resolve_table_meta(root, base_config, clusters)
            meta = table_metas.get(batch.name)
            cluster = clusters.get(meta.cluster) if meta and meta.cluster else None
            config = effective_config(base_config, batch, cluster=cluster)
            table_name = batch.name

        cache = BuildCache(resolve(root, config.defaults.cache_dir))

        out_paths, was_built = build(
            source_paths, registry, config, out_dir, cache=cache,
            reader_name=body.get("from"), writer_name=body.get("to"),
            force=bool(body.get("force", False)), dry_run=bool(body.get("dry_run", False)),
            cli_opts=body.get("opt") or None, keep_intermediate=bool(body.get("keep_intermediate", False)),
            table_name=table_name,
        )
        cache.save()

        log_event(root, "build", f"'{table_name}' → {', '.join(str(p) for p in out_paths)} ({'built' if was_built else 'from cache'})")

        golden_status = None
        if body.get("check_golden") and not body.get("dry_run"):
            history = HistoryStore(root)
            result = check_golden(history, table_name, source_paths, out_paths)
            golden_status = result.status
            if result.status == "mismatch":
                raise GoldenMismatchError(table_name)
            if result.status == "stale":
                raise GoldenStaleError(table_name)

        return {
            "outputs": [str(p) for p in out_paths],
            "was_built": was_built,
            "dry_run": bool(body.get("dry_run", False)),
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
        raise InvalidRequestError("'jobs' parameter must be an integer")
    if not (1 <= jobs <= MAX_BUILD_ALL_JOBS):
        raise InvalidRequestError(f"'jobs' parameter must be between 1 and {MAX_BUILD_ALL_JOBS}")
    filter_glob = params.get("filter")
    cluster = params.get("cluster")
    force = params.get("force") == "true"
    dry_run = params.get("dry_run") == "true"
    check_golden_flag = params.get("check_golden") == "true"

    async def event_gen():
        q: queue.Queue = queue.Queue()
        DONE = object()

        def on_table_result(ref, status):
            q.put(("progress", {"source": ref.name, "status": status}))

        def worker():
            try:
                registry = load_plugins(project_root=root)
                base_config = load_config(root)
                cache_dir = resolve(root, base_config.defaults.cache_dir)
                cache = BuildCache(cache_dir)
                sources = discover_table_sources(root, resolve(root, base_config.defaults.output_dir), cache_dir, filter_glob)

                duplicates = find_duplicate_stems(sources)
                if duplicates:
                    raise DuplicateTableNameError(duplicates)

                # batch tables aren't filtered by 'filter' (it filters
                # files on disk by path, batch tables are declared by
                # name in config) — always included in full.
                batch_tables = resolve_batch_tables(root, base_config)
                sources = exclude_batch_members(sources, batch_tables)
                check_no_batch_name_collisions(sources, batch_tables)
                tables = all_table_refs(sources, batch_tables)

                if cluster:
                    # Same post-discovery filter as 'pld build-all
                    # --cluster' (cli.py) — applies uniformly to
                    # single-file and batch tables, unlike 'filter'.
                    all_clusters = resolve_clusters(root, base_config)
                    if cluster not in all_clusters:
                        raise ClusterError(cluster, "no \\[\\[cluster]] with this name")
                    table_metas = resolve_table_meta(root, base_config, all_clusters)
                    tables = [
                        t for t in tables
                        if (m := table_metas.get(t.name)) is not None and m.cluster == cluster
                    ]

                summary = run_batch_build(
                    tables, root, registry, cache, out_dir, jobs=jobs, writer_name=to,
                    force=force, dry_run=dry_run, check_golden_flag=check_golden_flag,
                    on_table_result=on_table_result,
                )
                q.put(("summary", _summary_to_dict(summary)))
            except PayloadError as e:
                q.put(("error", e.to_dict()))
            except Exception:
                logging.getLogger("payload.web").exception("build-all: internal error")
                q.put(("error", {"error": "InternalError", "message": "Unexpected internal error"}))
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


async def preview_diff_route(request: Request) -> JSONResponse:
    """Live build-diff: builds the table into a TEMP folder
    (.payload_cache/preview/<table>, never the real output) and diffs
    the result byte-by-byte against the golden (if set) or the current
    output on disk. The frontend renders the changed runs with the
    reader's comments; Accept re-builds for real + commits, Discard
    does nothing (the real output is untouched)."""
    table = request.path_params["table_name"]
    body = await request.json()
    root = request.app.state.root

    def _run():
        from payload.core.discovery import discover_for_history, resolve_table_ref
        from payload.core.errors import TableNotFoundError

        sources, batch_tables, _ = discover_for_history(root)
        ref = resolve_table_ref(sources, batch_tables, table)
        if ref is None:
            raise TableNotFoundError(table)
        registry = load_plugins(project_root=root)

        if ref.is_batch:
            base_config = load_config(root)
            batch = next((b for b in resolve_batch_tables(root, base_config) if b.name == table), None)
            clusters = resolve_clusters(root, base_config)
            table_metas = resolve_table_meta(root, base_config, clusters)
            meta = table_metas.get(table)
            cluster = clusters.get(meta.cluster) if meta and meta.cluster else None
            config = effective_config(base_config, batch, cluster=cluster)
        else:
            config = load_config(root, source_path=ref.source_paths[0])

        preview_dir = resolve(root, f".payload_cache/preview/{table}")
        preview_dir.mkdir(parents=True, exist_ok=True)
        # clear stale preview outputs so a removed writer can't leave ghosts
        for stale in preview_dir.glob(f"{table}.*"):
            stale.unlink(missing_ok=True)

        build(
            ref.source_paths, registry, config, preview_dir, cache=None,
            reader_name=body.get("from") or None, writer_name=body.get("to") or None,
            force=True, table_name=ref.name,
        )

        history = HistoryStore(root)
        golden_id = history.golden_snapshot_id(table)
        real_out = resolve(root, config.defaults.output_dir)
        preview_outputs = sorted(preview_dir.glob(f"{ref.name}.*"))
        outputs = []
        for p in preview_outputs:
            cur = p.read_bytes()
            if golden_id is not None:
                snap = history.get_snapshot(table, golden_id)
                blob_hash = snap.output_blobs.get(p.name)
                prev = history.read_blob(blob_hash) if blob_hash else b""
                baseline = "golden"
                # golden exists but has no output for THIS file: the
                # file is new, "every byte differs" would be noise
                new_file = blob_hash is None
            else:
                real = real_out / p.name
                prev = real.read_bytes() if real.is_file() else b""
                baseline = "current"
                new_file = not real.is_file()
            runs = []
            if not new_file:
                max_len = max(len(cur), len(prev))
                for i in range(0, max_len, 8):
                    c, e = cur[i:i + 8], prev[i:i + 8]
                    if c != e:
                        runs.append({"offset": i, "current": c.hex(" "), "previous": e.hex(" ")})
            # full blobs (capped) so the frontend can render a real
            # side-by-side compare with highlights, not just the runs
            COMPARE_CAP = 65536
            outputs.append({
                "filename": p.name, "size": len(cur), "new_file": new_file, "runs": runs,
                "prev_base64": base64.b64encode(prev[:COMPARE_CAP]).decode("ascii"),
                "cur_base64": base64.b64encode(cur[:COMPARE_CAP]).decode("ascii"),
            })
        return {
            "table": table,
            "preview_dir": str(preview_dir),
            "baseline": baseline,
            "golden_snapshot_id": golden_id,
            "outputs": outputs,
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/build", build_route, methods=["POST"]),
    Route("/api/table/{table_name}/preview-diff", preview_diff_route, methods=["POST"]),
    Route("/api/build-all/stream", build_all_stream, methods=["GET"]),
]
