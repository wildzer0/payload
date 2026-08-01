"""
Pipeline: sorgente -> [stage] -> [stage] -> ... -> output.

Modello unico (vedi src/payload/docs/PIPELINE.md): anche un singolo reader+writer
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
    FanOutWriteError,
    InvalidPipelineError,
    NoWriterFoundError,
    PayloadError,
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

    # Stessa priorità del writer sotto: esplicito (--from) prima di
    # config.defaults.reader, che a sua volta prevale sull'auto-
    # risoluzione da estensione/sniff che find_reader() farebbe con
    # explicit=None.
    reader = registry.find_reader(source_path, reader_name or config.defaults.reader)

    # Risoluzione del writer, in ordine di priorità:
    # 1. esplicito (--to)
    # 2. config.defaults.writer, SOLO se qualcuno l'ha impostato davvero
    # 3. suggerimento del reader (reader.default_writer)
    # 4. errore chiaro, invece di indovinare
    wname = writer_name or config.defaults.writer or getattr(reader, "default_writer", None)
    if wname is None:
        raise WriterNotSpecifiedError(source_path, reader.name)
    if wname not in registry.writers:
        raise NoWriterFoundError(wname)

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


def final_output_paths(
    spec: PipelineSpec, source_path: Path, out_dir: Path, registry: PluginRegistry
) -> list[Path]:
    """Un path per ogni stage terminale della pipeline — normalmente
    uno solo, ma un fan-out (reader -> più writer consecutivi) ne
    produce uno per writer, tutti dalla stessa IR."""
    last = spec.stages[-1]
    if isinstance(last, WriterStage):
        start = spec.terminal_writer_start()
        return [
            out_dir / f"{source_path.stem}{registry.writers[s.name].extension}"
            for s in spec.stages[start:]
        ]
    # ExecStage come ultimo stage: validate_alternation garantisce che
    # output_extension sia sempre presente a questo punto.
    return [out_dir / f"{source_path.stem}{last.output_extension}"]


def resolve_table_outputs(
    source_path: Path, registry: PluginRegistry, config: "PayloadConfig", out_dir: Path
) -> tuple[list[Path], str | None, list[str]]:
    """Risolve la pipeline configurata ORA per la tabella e ritorna
    (output_paths, reader_name, writer_names) — usato da status/commit
    per sapere ESATTAMENTE quali file appartengono alla configurazione
    attuale, invece di un semplice glob '{stem}.*' che raccoglierebbe
    anche output orfani lasciati da un writer/pipeline precedente (es.
    dopo un cambio di writer, o dopo un restore a uno snapshot con un
    set di output diverso): senza questo, un commit successivo li
    riassorbirebbe per sbaglio come se facessero ancora parte dello
    stato attuale della tabella.

    Se la pipeline non si risolve (config incompleta, plugin mancante),
    ritorna ([], None, []) — chi chiama ricade sul comportamento
    precedente (glob non filtrato) per non bloccare status/commit su un
    errore che riguarda propriamente solo la build."""
    try:
        spec = resolve_pipeline_spec(source_path, registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)
    except PayloadError:
        return [], None, []
    out_paths = final_output_paths(spec, source_path, out_dir, registry)
    reader = next((s.name for s in spec.stages if isinstance(s, ReaderStage)), None)
    writers = [s.name for s in spec.stages if isinstance(s, WriterStage)]
    return out_paths, reader, writers


def _clean_stale_outputs(out_dir: Path, table_stem: str, keep_names: set[str]) -> None:
    """Rimuove dalla cartella di output i file di QUESTA tabella che non
    fanno più parte della pipeline appena risolta — es. si è cambiato
    writer (anche solo per questa singola build, con --to/--from) e il
    file del writer precedente è rimasto lì: altrimenti resterebbe un
    orfano pronto a essere riassorbito per sbaglio nel prossimo commit
    (che guarda semplicemente cosa c'è sul disco). Un fan-out (reader ->
    più writer in UNA pipeline) non è toccato: tutti i suoi output sono
    in keep_names, dato che vengono dalla stessa risoluzione."""
    if not out_dir.exists():
        return
    for existing in out_dir.glob(f"{table_stem}.*"):
        if existing.is_file() and existing.name not in keep_names:
            existing.unlink()
            logger.debug("Rimosso output orfano di una build precedente: %s", existing)


def describe_pipeline(spec: PipelineSpec) -> str:
    parts = []
    for s in spec.stages:
        if isinstance(s, (ReaderStage, WriterStage)):
            parts.append(f"{s.kind}:{s.name}")
        else:
            parts.append(f"exec:'{s.command}'")
    return " -> ".join(parts)


def describe_table_build(
    source_path: Path, registry: PluginRegistry, config: "PayloadConfig", output_paths: list[Path], out_dir: Path
) -> dict:
    """Descrive ESATTAMENTE come sono stati prodotti gli output che si
    stanno per committare — usato per annotare uno snapshot in modo
    fedele a cosa è successo davvero, non a cosa la config risolverebbe
    ORA (che potrebbe non corrispondere: un override ad-hoc --to/--from
    passato a QUESTA build specifica non viene mai scritto in config).

    Il writer è dedotto dall'ESTENSIONE dei file che si stanno
    realmente committando — un file '.h' è stato per forza scritto dal
    writer che dichiara quell'estensione, indipendentemente da cosa
    dice la config in questo momento. Il reader resta invece una
    risoluzione best-effort dalla config: non c'è modo di recuperare a
    posteriori un eventuale reader passato come override ad-hoc, il
    file sorgente non porta con sé quell'informazione.

    missing_outputs: confronta gli output ATTESI dalla pipeline
    risolta ORA con quelli che si stanno realmente committando — utile
    a marcare uno snapshot nato da un fan-out parziale (vedi
    FanOutWriteError): se un writer del gruppo è fallito, il suo file
    semplicemente non esiste su disco e non finisce in output_paths,
    ma la pipeline configurata continua ad aspettarselo. Committare
    comunque lo stato parziale non deve passare inosservato."""
    pipeline_explicit = bool(config.pipeline_stages)
    reader = None
    pipeline_description = None
    missing_outputs: list[str] = []
    try:
        spec = resolve_pipeline_spec(source_path, registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)
        reader = next((s.name for s in spec.stages if isinstance(s, ReaderStage)), None)
        if pipeline_explicit:
            pipeline_description = describe_pipeline(spec)
        expected = final_output_paths(spec, source_path, out_dir, registry)
        committed_names = {p.name for p in output_paths}
        missing_outputs = [p.name for p in expected if p.name not in committed_names]
    except PayloadError:
        pass

    writers = []
    for p in output_paths:
        match = next((w.name for w in registry.writers.values() if w.extension == p.suffix), None)
        if match and match not in writers:
            writers.append(match)

    return {
        "reader": reader,
        "writers": writers,
        "pipeline_explicit": pipeline_explicit,
        "pipeline_description": pipeline_description,
        "missing_outputs": missing_outputs,
    }


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


def _persist_stage_checkpoint(
    spec: PipelineSpec,
    table_key: str,
    table_name: str,
    stage_index: int,
    emitted_path: Path,
    config_dict: dict,
    source_bytes: bytes,
    cache: BuildCache,
) -> None:
    """Persiste l'output di uno stage NON terminale (writer/exec, con
    qualcosa dopo di sé nella pipeline) FUORI da tmp/ — che viene
    ripulita ad ogni build — così sopravvive alla build successiva ed è
    riusabile come checkpoint."""
    artifact_dir = _stage_artifact_dir(cache)
    persisted_path = artifact_dir / f"{table_name}_stage{stage_index}{emitted_path.suffix}"
    if emitted_path != persisted_path:
        shutil.copy2(emitted_path, persisted_path)
    checkpoint_key = compute_pipeline_cache_key(
        source_bytes, spec.signature_prefix(stage_index), config_dict
    )
    cache.update(_stage_checkpoint_key(table_key, stage_index), checkpoint_key, persisted_path)
    logger.debug("Checkpoint stage %d salvato: %s", stage_index, persisted_path)


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
    # eseguibili con argomenti — vedi src/payload/docs/PIPELINE.md, sezione
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
    final_out_paths: list[Path],
    cache: BuildCache | None = None,
    source_bytes: bytes | None = None,
    force: bool = False,
) -> list[Path]:
    table_name = source_path.stem
    n_stages = len(spec.stages)
    table_key = str(source_path)
    terminal_start = spec.terminal_writer_start()

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

    results: list[Path] = []
    i = start_index
    while i < n_stages:
        stage = spec.stages[i]

        if isinstance(stage, ReaderStage):
            reader = registry.readers[stage.name]
            logger.debug("Stage %d/%d: reader '%s'", i + 1, n_stages, stage.name)
            t0 = time.perf_counter()
            current_ir = reader.parse(current_path, config_dict)
            logger.debug("Parse completato in %.3fs", time.perf_counter() - t0)
            current_path = None
            i += 1

        elif isinstance(stage, WriterStage):
            # Un reader alimenta l'intero gruppo di writer consecutivi
            # che lo segue con la STESSA IR (fan-out) — parse() sopra è
            # già stato chiamato una sola volta per l'intero gruppo.
            fan_out_size = n_stages - terminal_start
            fan_out_succeeded: list[Path] = []
            fan_out_failures: list[tuple[str, str]] = []
            while i < n_stages and isinstance(spec.stages[i], WriterStage):
                writer_stage = spec.stages[i]
                writer = registry.writers[writer_stage.name]
                in_terminal_group = i >= terminal_start
                # Un vero fan-out (più di un writer terminale) tratta ogni
                # writer come indipendente: se uno fallisce, gli altri
                # vengono comunque tentati, altrimenti un solo writer
                # difettoso nasconderebbe che gli altri N-1 sono stati
                # scritti su disco con successo (vedi FanOutWriteError,
                # sollevata solo a gruppo terminato). Un writer singolo
                # o non-terminale mantiene il comportamento invariato:
                # fallisce e basta, non c'è nulla di "parziale" da salvare.
                resilient = in_terminal_group and fan_out_size > 1
                stage_out = (
                    final_out_paths[i - terminal_start] if in_terminal_group
                    else tmp_dir / f"stage{i}{writer.extension}"
                )
                logger.debug(
                    "Stage %d/%d: writer '%s' -> %s", i + 1, n_stages, writer_stage.name, stage_out
                )
                t0 = time.perf_counter()
                try:
                    emitted = writer.emit(current_ir, stage_out, config_dict)
                except Exception as e:
                    if not resilient:
                        raise
                    fan_out_failures.append((writer_stage.name, getattr(e, "message", str(e))))
                    i += 1
                    continue
                logger.debug("Emit completato in %.3fs", time.perf_counter() - t0)

                if in_terminal_group:
                    results.append(emitted)
                    if resilient:
                        fan_out_succeeded.append(emitted)
                else:
                    current_path = emitted
                    # Non terminale = c'è dell'altro dopo (reader/exec):
                    # checkpoint utile, coperto dalla cache di tabella
                    # solo se questo era l'ultimo stage, che qui non è.
                    if cache is not None:
                        _persist_stage_checkpoint(
                            spec, table_key, table_name, i, emitted, config_dict, source_bytes, cache
                        )
                i += 1
            if fan_out_failures:
                raise FanOutWriteError(fan_out_succeeded, fan_out_failures)
            current_ir = None

        else:  # ExecStage
            is_last = i == n_stages - 1
            stage_out = final_out_paths[0] if is_last else tmp_dir / f"stage{i}.bin"
            logger.debug("Stage %d/%d: exec '%s' -> %s", i + 1, n_stages, stage.command, stage_out)
            current_path = _run_exec_stage(stage, current_path, stage_out, table_name)
            if is_last:
                results.append(current_path)
            elif cache is not None:
                _persist_stage_checkpoint(
                    spec, table_key, table_name, i, current_path, config_dict, source_bytes, cache
                )
            i += 1

    return results


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
) -> tuple[list[Path], bool]:
    """Ritorna (output_paths, was_built). was_built=False se servito da
    cache. output_paths ha un solo elemento nel caso comune, più di uno
    se la pipeline termina in un fan-out (reader -> più writer).

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
        out_paths = final_output_paths(spec, source_path, out_dir, registry)

        if not dry_run:
            _clean_stale_outputs(out_dir, source_path.stem, {p.name for p in out_paths})

        if cache is not None and not force and cache.is_fresh(table_key, cache_key):
            logger.info("Cache hit, skip build")
            return out_paths, False

        if dry_run:
            logger.info("[dry-run] pipeline: %s -> %s", describe_pipeline(spec), out_paths)
            return out_paths, True

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = source_path.parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            result_paths = _execute_stages(
                spec, source_path, registry, config_dict, tmp_dir, out_paths,
                cache=cache, source_bytes=source_bytes, force=force,
            )
        finally:
            if not keep_intermediate:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if cache is not None:
            cache.update(table_key, cache_key, result_paths)

        logger.info("Build completata: %s -> %s", source_path.name, result_paths)
        return result_paths, True
    finally:
        current_table.reset(token)
