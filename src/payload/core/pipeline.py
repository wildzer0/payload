"""
Pipeline: sorgente -> [Reader] -> TableIR -> [Writer] -> output.

Il core qui è deliberatamente "stupido": tutta la logica interessante
vive nei plugin. Questo è voluto — aggiungere un formato non tocca mai
questa funzione.
"""
from __future__ import annotations

import contextvars
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.cache import BuildCache, compute_cache_key
from payload.core.errors import NoWriterFoundError, SourceNotFoundError, WriterEmitError, WriterNotSpecifiedError
from payload.core.registry import PluginRegistry

if TYPE_CHECKING:
    from payload.core.config import PayloadConfig

logger = logging.getLogger(__name__)

# usato dal logging setup per taggare ogni riga di log con la tabella corrente,
# anche in build paralleli (thread pool) — vedi core/logging_setup.py
current_table: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_table", default=None
)


def build(
    source_path: Path,
    registry: PluginRegistry,
    config: "PayloadConfig",
    out_dir: Path,
    cache: BuildCache | None = None,
    reader_name: str | None = None,
    writer_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    cli_opts: dict | None = None,
) -> tuple[Path, bool]:
    """Ritorna (output_path, was_built). was_built=False se servito da cache.

    cli_opts: override una tantum da --opt chiave=valore, validi solo
    per questa invocazione (non persistono in nessun file). Un
    reader/writer li legge con config.get("cli_opts", {}).get("chiave").
    Per valori che devono persistere, la sede giusta è invece
    [plugin.<nome>] in table-tool.toml/sidecar (config.plugin nel
    PayloadConfig, già incluso in config.model_dump())."""
    if not source_path.exists():
        raise SourceNotFoundError(source_path)

    token = current_table.set(source_path.stem)
    try:
        reader = registry.find_reader(source_path, reader_name)

        # Risoluzione del writer, in ordine di priorità:
        # 1. esplicito (--to)
        # 2. config.defaults.writer, SOLO se qualcuno l'ha impostato davvero
        #    (non un fallback silenzioso — vedi core/config.py, default None)
        # 3. suggerimento del reader (reader.default_writer)
        # 4. errore chiaro, invece di indovinare
        wname = writer_name or config.defaults.writer or getattr(reader, "default_writer", None)
        if wname is None:
            raise WriterNotSpecifiedError(source_path, reader.name)
        if wname not in registry.writers:
            raise NoWriterFoundError(wname)
        writer = registry.writers[wname]

        # Compatibilità dichiarata dal writer, controllata PRIMA di parsare
        # (evita di sprecare lavoro su una combinazione che fallirà comunque,
        # ed evita soprattutto di produrre output sbagliato in silenzio).
        compatible = getattr(writer, "compatible_readers", None)
        if compatible is not None and reader.name not in compatible:
            raise WriterEmitError(
                writer.name,
                f"non compatibile con il reader '{reader.name}' — "
                f"formati compatibili: {', '.join(compatible)}",
            )

        source_bytes = source_path.read_bytes()
        config_dict = config.model_dump()
        if cli_opts:
            config_dict["cli_opts"] = cli_opts
        cache_key = compute_cache_key(source_bytes, reader.name, writer.name, config_dict)
        table_key = str(source_path)
        out_path = out_dir / f"{source_path.stem}{writer.extension}"

        if cache is not None and not force and cache.is_fresh(table_key, cache_key):
            logger.info("Cache hit, skip build")
            return out_path, False

        if dry_run:
            logger.info("[dry-run] verrebbe rigenerato: %s", out_path)
            return out_path, True

        logger.debug("Reader selezionato: %s", reader.name)
        t0 = time.perf_counter()
        ir = reader.parse(source_path, config_dict)
        logger.debug("Parse completato in %.3fs", time.perf_counter() - t0)

        out_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Writer selezionato: %s", writer.name)
        t0 = time.perf_counter()
        result_path = writer.emit(ir, out_path, config_dict)
        logger.debug("Emit completato in %.3fs", time.perf_counter() - t0)

        if cache is not None:
            cache.update(table_key, cache_key, result_path)

        logger.info("Build completata: %s -> %s", source_path.name, result_path)
        return result_path, True
    finally:
        current_table.reset(token)
