"""
Pipeline: sorgente -> [stage] -> [stage] -> ... -> output.

Modello unico (vedi docs/PIPELINE.md): anche un singolo reader+writer
e' internamente una pipeline a 2 stage, costruita implicitamente da
--from/--to quando non c'e' una pipeline esplicita in config. Un solo
motore di esecuzione per ogni build, niente casi speciali in parallelo.
"""
from __future__ import annotations

import contextvars
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.cache import BuildCache, compute_pipeline_cache_key
from payload.core.errors import (
    InvalidPipelineError,
    SourceNotFoundError,
    ToolchainExecutionError,
    WriterEmitError,
    WriterNotSpecifiedError,
)
from payload.core.ir import TableIR
from payload.core.pipeline_spec import ExecStage, PipelineSpec, ReaderStage, WriterStage
from payload.core.registry import PluginRegistry

if TYPE_CHECKING:
    from payload.core.config import PayloadConfig

logger = logging.getLogger(__name__)

# usato dal logging setup per taggare ogni riga di log con la tabella corrente,
# anche in build paralleli (thread pool) — vedi core/logging_setup.py
current_table: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_table", default=None
)


def resolve_pipeline_spec(
    source_path: Path,
    registry: PluginRegistry,
    config: "PayloadConfig",
    reader_name: str | None,
    writer_name: str | None,
) -> PipelineSpec:
    """Pipeline esplicita da config se presente, altrimenti quella
    implicita a 2 stage costruita da --from/--to — stessa identica
    risoluzione di prima dell'introduzione della pipeline."""
    if config.pipeline_stages:
        if reader_name or writer_name:
            logger.warning(
                "Pipeline esplicita configurata per questa tabella: --from/--to ignorati"
            )
        return PipelineSpec.from_raw_stages(config.pipeline_stages)

    reader = registry.find_reader(source_path, reader_name)

    # Risoluzione del writer, in ordine di priorità:
    # 1. esplicito (--to)
    # 2. config.defaults.writer, SOLO se qualcuno l'ha impostato davvero
    # 3. suggerimento del reader (reader.default_writer)
    # 4. errore chiaro, invece di indovinare
    wname = writer_name or config.defaults.writer or getattr(reader, "default_writer", None)
    if wname is None:
        raise WriterNotSpecifiedError(source_path, reader.name)

    return PipelineSpec.implicit(reader.name, wname)


def validate_pipeline_against_registry(spec: PipelineSpec, registry: PluginRegistry) -> None:
    """Nomi di stage sconosciuti e compatibilità reader/writer, per
    OGNI coppia adiacente nella pipeline — controllato PRIMA di
    eseguire qualunque stage, non a metà di una build costosa."""
    for i, stage in enumerate(spec.stages):
        if isinstance(stage, ReaderStage) and stage.name not in registry.readers:
            raise InvalidPipelineError(i, f"reader sconosciuto: '{stage.name}' (vedi 'pld plugins')")
        if isinstance(stage, WriterStage) and stage.name not in registry.writers:
            raise InvalidPipelineError(i, f"writer sconosciuto: '{stage.name}' (vedi 'pld plugins')")

    for reader_stage, writer_stage in spec.reader_writer_pairs():
        writer = registry.writers[writer_stage.name]
        compatible = getattr(writer, "compatible_readers", None)
        if compatible is not None and reader_stage.name not in compatible:
            raise WriterEmitError(
                writer_stage.name,
                f"non compatibile con il reader '{reader_stage.name}' — "
                f"formati compatibili: {', '.join(compatible)}",
            )


def final_output_path(
    spec: PipelineSpec, source_path: Path, out_dir: Path, registry: PluginRegistry
) -> Path:
    last = spec.stages[-1]
    if isinstance(last, WriterStage):
        writer = registry.writers[last.name]
        return out_dir / f"{source_path.stem}{writer.extension}"
    # ExecStage come ultimo stage: validate_alternation garantisce che
    # output_extension sia sempre presente a questo punto.
    return out_dir / f"{source_path.stem}{last.output_extension}"


def _describe_pipeline(spec: PipelineSpec) -> str:
    parts = []
    for s in spec.stages:
        if isinstance(s, (ReaderStage, WriterStage)):
            parts.append(f"{s.kind}:{s.name}")
        else:
            parts.append(f"exec:'{s.command}'")
    return " -> ".join(parts)


def _stage_artifact_dir(cache: BuildCache) -> Path:
    """Dove vivono i checkpoint di stage persistenti — DIVERSO da tmp/,
    che viene ripulita ad ogni build: i checkpoint devono sopravvivere
    tra una build e l'altra, altrimenti la cache per stage non
    servirebbe a nulla."""
    d = cache.cache_dir / "stage_artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_checkpoint_key(table_key: str, stage_index: int) -> str:
    return f"{table_key}::stage{stage_index}"


def _find_resumable_checkpoint(
    spec: PipelineSpec, table_key: str, source_bytes: bytes, config_dict: dict, cache: BuildCache
) -> tuple[int, Path] | None:
    """Cerca il checkpoint valido più avanzato da cui riprendere,
    partendo dall'ultimo stage e scendendo — il primo che trova valido
    è il migliore possibile, permette di saltare tutti gli stage prima
    di lui. Solo stage che producono un FILE (writer/exec) sono
    checkpoint validi: un reader produce IR in memoria, non c'è nulla
    da persistere/riusare a quel punto."""
    for i in range(len(spec.stages) - 1, -1, -1):
        if not isinstance(spec.stages[i], (WriterStage, ExecStage)):
            continue
        checkpoint_key = compute_pipeline_cache_key(
            source_bytes, spec.signature_prefix(i), config_dict
        )
        stage_table_key = _stage_checkpoint_key(table_key, i)
        if cache.is_fresh(stage_table_key, checkpoint_key):
            path = cache.get_output_path(stage_table_key)
            if path is not None and path.exists():
                return i, path
    return None


def _run_exec_stage(stage: ExecStage, input_path: Path, output_path: Path, table_name: str) -> Path:
    try:
        formatted_command = stage.command.format(
            input=str(input_path), output=str(output_path), table_name=table_name
        )
    except (KeyError, IndexError) as e:
        raise ToolchainExecutionError(
            [stage.command], -1, f"placeholder sconosciuto nel comando: {e}"
        ) from e

    logger.debug("Eseguo stage exec: %s", formatted_command)
    # shell=True e' voluto qui: uno stage 'exec' e' letteralmente un
    # comando shell scritto dall'utente in config (pipe, redirect,
    # eseguibili con argomenti — vedi docs/PIPELINE.md, sezione
    # Sicurezza, per le implicazioni).
    result = subprocess.run(formatted_command, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        if stage.on_error == "warn":
            logger.warning(
                "Stage exec fallito (on_error='warn', proseguo con l'input invariato): %s",
                result.stderr.strip() or f"exit {result.returncode}",
            )
            # copia (non solo 'ricorda') l'input nella posizione attesa:
            # se questo era l'ultimo stage, output_path e' final_out_path,
            # fuori da tmp/ — lasciare il risultato dentro tmp/ lo farebbe
            # sparire al cleanup di fine build, rompendo il contratto
            # "ogni stage produce un file nella propria posizione attesa"
            shutil.copy2(input_path, output_path)
            return output_path
        raise ToolchainExecutionError([formatted_command], result.returncode, result.stderr)

    if not output_path.exists():
        raise ToolchainExecutionError(
            [formatted_command], result.returncode,
            f"il comando e' terminato con successo ma non ha prodotto il file atteso: {output_path}",
        )

    return output_path


def _execute_stages(
    spec: PipelineSpec,
    source_path: Path,
    registry: PluginRegistry,
    config_dict: dict,
    tmp_dir: Path,
    final_out_path: Path,
    cache: BuildCache | None = None,
    source_bytes: bytes | None = None,
    force: bool = False,
) -> Path:
    table_name = source_path.stem
    n_stages = len(spec.stages)
    table_key = str(source_path)

    start_index = 0
    current_path: Path | None = source_path
    current_ir: TableIR | None = None

    if cache is not None and not force and source_bytes is not None:
        resumable = _find_resumable_checkpoint(spec, table_key, source_bytes, config_dict, cache)
        if resumable is not None:
            checkpoint_index, checkpoint_path = resumable
            logger.debug(
                "Ripreso da checkpoint stage %d/%d (%d stage saltati): %s",
                checkpoint_index + 1, n_stages, checkpoint_index + 1, checkpoint_path,
            )
            current_path = checkpoint_path
            start_index = checkpoint_index + 1

    for i in range(start_index, n_stages):
        stage = spec.stages[i]
        is_last = i == n_stages - 1

        if isinstance(stage, ReaderStage):
            reader = registry.readers[stage.name]
            logger.debug("Stage %d/%d: reader '%s'", i + 1, n_stages, stage.name)
            t0 = time.perf_counter()
            current_ir = reader.parse(current_path, config_dict)
            logger.debug("Parse completato in %.3fs", time.perf_counter() - t0)
            current_path = None

        elif isinstance(stage, WriterStage):
            writer = registry.writers[stage.name]
            stage_out = final_out_path if is_last else tmp_dir / f"stage{i}{writer.extension}"
            logger.debug("Stage %d/%d: writer '%s' -> %s", i + 1, n_stages, stage.name, stage_out)
            t0 = time.perf_counter()
            current_path = writer.emit(current_ir, stage_out, config_dict)
            logger.debug("Emit completato in %.3fs", time.perf_counter() - t0)
            current_ir = None

        else:  # ExecStage
            stage_out = final_out_path if is_last else tmp_dir / f"stage{i}.bin"
            logger.debug("Stage %d/%d: exec '%s' -> %s", i + 1, n_stages, stage.command, stage_out)
            current_path = _run_exec_stage(stage, current_path, stage_out, table_name)

        # Checkpoint: solo per stage che producono un file (writer/exec)
        # e solo se NON è l'ultimo (quello lo gestisce già la cache
        # dell'intera pipeline in build()). Persistito FUORI da tmp/,
        # altrimenti sparirebbe al cleanup e il checkpoint sarebbe inutile
        # alla build successiva.
        if cache is not None and not is_last and isinstance(stage, (WriterStage, ExecStage)):
            artifact_dir = _stage_artifact_dir(cache)
            persisted_path = artifact_dir / f"{table_name}_stage{i}{current_path.suffix}"
            if current_path != persisted_path:
                shutil.copy2(current_path, persisted_path)
            checkpoint_key = compute_pipeline_cache_key(
                source_bytes, spec.signature_prefix(i), config_dict
            )
            cache.update(_stage_checkpoint_key(table_key, i), checkpoint_key, persisted_path)
            logger.debug("Checkpoint stage %d salvato: %s", i, persisted_path)

    return current_path


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
    keep_intermediate: bool = False,
) -> tuple[Path, bool]:
    """Ritorna (output_path, was_built). was_built=False se servito da cache.

    cli_opts: override una tantum da --opt chiave=valore, validi solo
    per questa invocazione. keep_intermediate: non ripulisce tmp/ dopo
    la build, utile per ispezionare i file intermedi di una pipeline
    multi-stage in fase di debug."""
    if not source_path.exists():
        raise SourceNotFoundError(source_path)

    token = current_table.set(source_path.stem)
    try:
        spec = resolve_pipeline_spec(source_path, registry, config, reader_name, writer_name)
        validate_pipeline_against_registry(spec, registry)

        source_bytes = source_path.read_bytes()
        config_dict = config.model_dump()
        if cli_opts:
            config_dict["cli_opts"] = cli_opts

        cache_key = compute_pipeline_cache_key(source_bytes, spec.cache_signature(), config_dict)
        table_key = str(source_path)
        out_path = final_output_path(spec, source_path, out_dir, registry)

        if cache is not None and not force and cache.is_fresh(table_key, cache_key):
            logger.info("Cache hit, skip build")
            return out_path, False

        if dry_run:
            logger.info("[dry-run] pipeline: %s -> %s", _describe_pipeline(spec), out_path)
            return out_path, True

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = source_path.parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            result_path = _execute_stages(
                spec, source_path, registry, config_dict, tmp_dir, out_path,
                cache=cache, source_bytes=source_bytes, force=force,
            )
        finally:
            if not keep_intermediate:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if cache is not None:
            cache.update(table_key, cache_key, result_path)

        logger.info("Build completata: %s -> %s", source_path.name, result_path)
        return result_path, True
    finally:
        current_table.reset(token)
