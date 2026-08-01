"""
Motore di batch-build: date N tabelle (file singoli o tabelle batch,
vedi core/discovery.py::TableRef), le costruisce in un thread pool e
aggrega i risultati — condiviso tra 'pld build-all' e la build iniziale
di 'pld watch', così la logica vive in un solo posto.

Nessuna dipendenza da Rich qui: la UI (progress bar) resta nel comando
CLI, agganciata tramite 'on_table_result'. Questo modulo non solleva
mai un'eccezione aggregata (niente BatchBuildError) — ritorna i
fallimenti dentro BatchBuildSummary e lascia al chiamante decidere se
e come farli esplodere (build-all vuole fallire il comando, watch no).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from payload.core.batch_tables import effective_config
from payload.core.cache import BuildCache
from payload.core.config import load_config
from payload.core.errors import PayloadError
from payload.core.golden import check_golden
from payload.core.history import HistoryStore
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry

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
    """Builda ogni tabella in 'tables' (jobs=1 sequenziale, jobs>1 su
    thread pool — le tabelle sono indipendenti tra loro), aggrega i
    risultati e salva la cache una volta sola alla fine.

    on_table_result(ref, status), se dato, è invocato dopo ogni build
    con status in "ok"/"error" — usato dal chiamante per aggiornare una
    progress bar, opzionale altrimenti."""
    summary = BatchBuildSummary()
    lock = threading.Lock()
    history = HistoryStore(root)  # sola lettura qui (check_golden), sicuro condiviso tra i thread

    def _build_one(ref: "TableRef"):
        """Eseguita in un thread del pool. Ritorna sempre, non solleva:
        gli errori sono catturati qui per non far crashare l'executor e
        per accumulare tutti i fallimenti, non solo il primo."""
        try:
            if ref.is_batch:
                per_table_config = effective_config(load_config(root), ref.batch)
            else:
                per_table_config = load_config(root, source_path=ref.source_paths[0])
            out_paths, was_built = build(
                ref.source_paths, registry, per_table_config, out_dir, cache=cache,
                writer_name=writer_name, force=force, dry_run=dry_run,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate, table_name=ref.name,
            )
            mismatch = False
            if check_golden_flag and not dry_run:
                # 'stale' conta come mismatch qui: il sorgente è cambiato dopo
                # il golden, quindi anche un output che sembra combaciare non è
                # una verifica affidabile — batch-build vuole un segnale netto.
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
