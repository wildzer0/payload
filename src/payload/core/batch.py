"""
Batch-build engine: given N tables (single files or batch tables, see
core/discovery.py::TableRef), builds them in a thread pool and
aggregates the results — shared between 'pld build-all' and the initial
build of 'pld watch', so the logic lives in one place.

No dependency on Rich here: the UI (progress bar) stays in the CLI
command, hooked in via 'on_table_result'. This module never raises an
aggregate exception (no BatchBuildError) — it returns failures inside
BatchBuildSummary and leaves it to the caller to decide whether and how
to blow up (build-all wants to fail the command, watch doesn't).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from payload.core.cache import BuildCache
from payload.core.clusters import resolve_clusters
from payload.core.config import load_config
from payload.core.discovery import resolve_table_config
from payload.core.errors import PayloadError
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry
from payload.core.table_meta import resolve_table_meta

if TYPE_CHECKING:
    from payload.core.discovery import TableRef

OnTableResult = Callable[["TableRef", str], None]


@dataclass
class BatchBuildSummary:
    built: int = 0
    cached: int = 0
    golden_mismatch: int = 0
    errors: int = 0
    failures: list[PayloadError] = field(default_factory=list)


def run_batch_build(
    tables: list["TableRef"],
    root: Path,
    registry: PluginRegistry,
    cache: BuildCache,
    out_dir: Path,
    *,
    jobs: int = 1,
    writer_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    check_golden_flag: bool = False,
    cli_opts: dict | None = None,
    keep_intermediate: bool = False,
    on_table_result: OnTableResult | None = None,
) -> BatchBuildSummary:
    """Builds every table in 'tables' (jobs=1 sequential, jobs>1 on a
    thread pool — tables are independent of each other), aggregates the
    results and saves the cache once at the end.

    on_table_result(ref, status), if given, is invoked after each build
    with status "ok"/"error" — used by the caller to update a progress
    bar, optional otherwise."""
    summary = BatchBuildSummary()
    lock = threading.Lock()
    history = HistoryStore(root)  # read-only here (check_golden), safe to share across threads

    # Resolved ONCE for the whole batch, not per table: every ref's
    # cluster lookup is a plain dict read from here on, instead of
    # every thread in the pool below re-parsing table-tool.toml's
    # [[cluster]]/[[table_meta]] on its own.
    base_config = load_config(root)
    clusters = resolve_clusters(root, base_config)
    table_metas = resolve_table_meta(root, base_config, clusters)

    def _build_one(ref: "TableRef"):
        """Runs in a pool thread. Always returns, never raises: errors
        are caught here so they don't crash the executor and so all
        failures accumulate, not just the first one."""
        try:
            per_table_config = resolve_table_config(root, base_config, ref, clusters, table_metas)
            out_paths, was_built = build(
                ref.source_paths, registry, per_table_config, out_dir, cache=cache,
                writer_name=writer_name, force=force, dry_run=dry_run,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate, table_name=ref.name,
            )
            mismatch = False
            if check_golden_flag and not dry_run:
                # 'stale' counts as a mismatch here: the source changed after
                # the golden was set, so even an output that looks like it
                # matches isn't a reliable check — batch-build wants a clear
                # signal.
                status = check_golden(history, ref.name, ref.source_paths, out_paths).status
                mismatch = status in ("mismatch", "stale")
            return ("ok", ref, was_built, mismatch, None)
        except PayloadError as e:
            return ("error", ref, False, False, e)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = {executor.submit(_build_one, ref): ref for ref in tables}
        for future in as_completed(futures):
            status, ref, was_built, mismatch, error = future.result()
            with lock:
                if status == "ok":
                    if was_built:
                        summary.built += 1
                    else:
                        summary.cached += 1
                    if mismatch:
                        summary.golden_mismatch += 1
                else:
                    summary.errors += 1
                    summary.failures.append(error)
            if on_table_result:
                on_table_result(ref, status)

    cache.save()
    return summary
