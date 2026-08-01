"""
Pipeline: source -> [stage] -> [stage] -> ... -> output.

Single model (see src/payload/docs/PIPELINE.md): even a single reader+writer
is internally a 2-stage pipeline, built implicitly from --from/--to
when there's no explicit pipeline in config. One execution engine for
every build, no special cases running in parallel.
"""
from __future__ import annotations

import contextvars
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.cache import BuildCache, compute_pipeline_cache_key, compute_pipeline_cache_key_multi
from payload.core.errors import (
    FanOutWriteError,
    InvalidPipelineError,
    NoWriterFoundError,
    PayloadError,
    ReaderBatchUnsupportedError,
    ReaderParseError,
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

# used by the logging setup to tag every log line with the current table,
# even in parallel builds (thread pool) — see core/logging_setup.py
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
    """Explicit pipeline from config if present, otherwise the
    implicit 2-stage one built from --from/--to — the exact same
    resolution as before the pipeline was introduced."""
    if config.pipeline_stages:
        if reader_name or writer_name:
            logger.warning(
                "Explicit pipeline configured for this table: --from/--to ignored"
            )
        return PipelineSpec.from_raw_stages(config.pipeline_stages)

    # Same priority as the writer below: explicit (--from) before
    # config.defaults.reader, which in turn takes precedence over the
    # extension/sniff auto-resolution that find_reader() would do with
    # explicit=None.
    reader = registry.find_reader(source_path, reader_name or config.defaults.reader)

    # Writer resolution, in priority order:
    # 1. explicit (--to)
    # 2. config.defaults.writer, ONLY if someone actually set it
    # 3. the reader's suggestion (reader.default_writer)
    # 4. a clear error, instead of guessing
    wname = writer_name or config.defaults.writer or getattr(reader, "default_writer", None)
    if wname is None:
        raise WriterNotSpecifiedError(source_path, reader.name)
    if wname not in registry.writers:
        raise NoWriterFoundError(wname)

    return PipelineSpec.implicit(reader.name, wname)


def validate_pipeline_against_registry(spec: PipelineSpec, registry: PluginRegistry) -> None:
    """Unknown stage names and reader/writer compatibility, for EVERY
    adjacent pair in the pipeline — checked BEFORE running any stage,
    not midway through an expensive build."""
    for i, stage in enumerate(spec.stages):
        if isinstance(stage, ReaderStage) and stage.name not in registry.readers:
            raise InvalidPipelineError(i, f"unknown reader: '{stage.name}' (see 'pld plugins')")
        if isinstance(stage, WriterStage) and stage.name not in registry.writers:
            raise InvalidPipelineError(i, f"unknown writer: '{stage.name}' (see 'pld plugins')")

    for reader_stage, writer_stage in spec.reader_writer_pairs():
        writer = registry.writers[writer_stage.name]
        compatible = getattr(writer, "compatible_readers", None)
        if compatible is not None and reader_stage.name not in compatible:
            raise WriterEmitError(
                writer_stage.name,
                f"not compatible with reader '{reader_stage.name}' — "
                f"compatible formats: {', '.join(compatible)}",
            )


def final_output_paths(
    spec: PipelineSpec, table_stem: str, out_dir: Path, registry: PluginRegistry
) -> list[Path]:
    """One path per terminal stage of the pipeline — normally just
    one, but a fan-out (reader -> several consecutive writers)
    produces one per writer, all from the same IR. table_stem is the
    table's logical name (the source's stem for a normal table,
    BatchTable.name for a batch table — see core/batch_tables.py):
    this function doesn't need to know which of the two it is, only
    the name that ends up in the output filename."""
    last = spec.stages[-1]
    if isinstance(last, WriterStage):
        start = spec.terminal_writer_start()
        return [
            out_dir / f"{table_stem}{registry.writers[s.name].extension}"
            for s in spec.stages[start:]
        ]
    # ExecStage as the last stage: validate_alternation guarantees
    # output_extension is always present at this point.
    return [out_dir / f"{table_stem}{last.output_extension}"]


def resolve_table_outputs(
    source_path: Path, registry: PluginRegistry, config: "PayloadConfig", out_dir: Path
) -> tuple[list[Path], str | None, list[str]]:
    """Resolves the pipeline configured RIGHT NOW for the table and
    returns (output_paths, reader_name, writer_names) — used by
    status/commit to know EXACTLY which files belong to the current
    configuration, instead of a plain '{stem}.*' glob that would also
    pick up orphaned outputs left by a previous writer/pipeline (e.g.
    after changing writer, or after restoring to a snapshot with a
    different set of outputs): without this, a later commit would
    accidentally reabsorb them as if they were still part of the
    table's current state.

    If the pipeline doesn't resolve (incomplete config, missing
    plugin), returns ([], None, []) — the caller falls back to the
    previous behavior (unfiltered glob) so as not to block status/
    commit on an error that's really only relevant to the build."""
    try:
        spec = resolve_pipeline_spec(source_path, registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)
    except PayloadError:
        return [], None, []
    out_paths = final_output_paths(spec, source_path.stem, out_dir, registry)
    reader = next((s.name for s in spec.stages if isinstance(s, ReaderStage)), None)
    writers = [s.name for s in spec.stages if isinstance(s, WriterStage)]
    return out_paths, reader, writers


def _clean_stale_outputs(out_dir: Path, table_stem: str, keep_names: set[str]) -> None:
    """Removes files belonging to THIS table from the output folder
    that are no longer part of the just-resolved pipeline — e.g. the
    writer was changed (even just for this single build, with
    --to/--from) and the previous writer's file was left there:
    otherwise it would stay orphaned, ready to be accidentally
    reabsorbed by the next commit (which simply looks at what's on
    disk). A fan-out (reader -> several writers in ONE pipeline) is
    untouched: all of its outputs are in keep_names, since they come
    from the same resolution."""
    if not out_dir.exists():
        return
    for existing in out_dir.glob(f"{table_stem}.*"):
        if existing.is_file() and existing.name not in keep_names:
            existing.unlink()
            logger.debug("Removed orphaned output from a previous build: %s", existing)


def describe_pipeline(spec: PipelineSpec) -> str:
    parts = []
    for s in spec.stages:
        if isinstance(s, (ReaderStage, WriterStage)):
            parts.append(f"{s.kind}:{s.name}")
        else:
            parts.append(f"exec:'{s.command}'")
    return " -> ".join(parts)


def describe_table_build(
    source_paths: list[Path], registry: PluginRegistry, config: "PayloadConfig",
    output_paths: list[Path], out_dir: Path, table_name: str | None = None,
) -> dict:
    """Describes EXACTLY how the outputs about to be committed were
    produced — used to annotate a snapshot faithfully to what actually
    happened, not to what the config would resolve to RIGHT NOW (which
    might not match: an ad-hoc --to/--from override passed to THIS
    specific build is never written back to config).

    The writer is inferred from the EXTENSION of the files actually
    being committed — a '.h' file was necessarily written by the
    writer that declares that extension, regardless of what the config
    says at this moment. The reader, on the other hand, is a
    best-effort resolution from config: there's no way to retroactively
    recover an ad-hoc override reader passed for this build, the
    source file doesn't carry that information with it.

    missing_outputs: compares the outputs EXPECTED by the pipeline as
    resolved RIGHT NOW against what's actually being committed —
    useful to flag a snapshot born from a partial fan-out (see
    FanOutWriteError): if one writer in the group failed, its file
    simply doesn't exist on disk and doesn't end up in output_paths,
    but the configured pipeline still expects it. Committing the
    partial state anyway must not go unnoticed.

    source_paths/table_name: same as build() — table_name=None derives
    the stem from the (single) source_paths[0], a batch table always
    passes it explicitly (the name isn't derived from any filename)."""
    table_name = table_name or source_paths[0].stem
    pipeline_explicit = bool(config.pipeline_stages)
    reader = None
    pipeline_description = None
    missing_outputs: list[str] = []
    try:
        spec = resolve_pipeline_spec(source_paths[0], registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)
        reader = next((s.name for s in spec.stages if isinstance(s, ReaderStage)), None)
        if pipeline_explicit:
            pipeline_description = describe_pipeline(spec)
        expected = final_output_paths(spec, table_name, out_dir, registry)
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
    """Where persistent stage checkpoints live — DIFFERENT from tmp/,
    which gets cleaned up on every build: checkpoints must survive
    across builds, otherwise per-stage caching would be pointless."""
    d = cache.cache_dir / "stage_artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_checkpoint_key(table_name: str, stage_index: int) -> str:
    return f"{table_name}::stage{stage_index}"


def _persist_stage_checkpoint(
    spec: PipelineSpec,
    table_name: str,
    stage_index: int,
    emitted_path: Path,
    config_dict: dict,
    source_bytes: bytes,
    cache: BuildCache,
) -> None:
    """Persists the output of a NON-terminal stage (writer/exec, with
    something after it in the pipeline) OUTSIDE tmp/ — which gets
    cleaned on every build — so it survives to the next build and is
    reusable as a checkpoint."""
    artifact_dir = _stage_artifact_dir(cache)
    persisted_path = artifact_dir / f"{table_name}_stage{stage_index}{emitted_path.suffix}"
    if emitted_path != persisted_path:
        shutil.copy2(emitted_path, persisted_path)
    checkpoint_key = compute_pipeline_cache_key(
        source_bytes, spec.signature_prefix(stage_index), config_dict
    )
    cache.update(_stage_checkpoint_key(table_name, stage_index), checkpoint_key, persisted_path)
    logger.debug("Stage %d checkpoint saved: %s", stage_index, persisted_path)


def _find_resumable_checkpoint(
    spec: PipelineSpec, table_name: str, source_bytes: bytes, config_dict: dict, cache: BuildCache
) -> tuple[int, Path] | None:
    """Looks for the most advanced valid checkpoint to resume from,
    starting from the last stage and going backward — the first valid
    one found is the best possible, letting all the stages before it
    be skipped. Only stages that produce a FILE (writer/exec) are
    valid checkpoints: a reader produces IR in memory, there's nothing
    to persist/reuse at that point."""
    for i in range(len(spec.stages) - 1, -1, -1):
        if not isinstance(spec.stages[i], (WriterStage, ExecStage)):
            continue
        checkpoint_key = compute_pipeline_cache_key(
            source_bytes, spec.signature_prefix(i), config_dict
        )
        stage_table_key = _stage_checkpoint_key(table_name, i)
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
            [stage.command], -1, f"unknown placeholder in the command: {e}"
        ) from e

    logger.debug("Running exec stage: %s", formatted_command)
    # shell=True is intentional here: an 'exec' stage is literally a
    # shell command written by the user in config (pipes, redirects,
    # executables with arguments — see src/payload/docs/PIPELINE.md, Security
    # section, for the implications).
    result = subprocess.run(formatted_command, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        if stage.on_error == "warn":
            logger.warning(
                "Exec stage failed (on_error='warn', continuing with the input unchanged): %s",
                result.stderr.strip() or f"exit {result.returncode}",
            )
            # copy (not just "remember") the input to the expected
            # location: if this was the last stage, output_path is
            # final_out_path, outside tmp/ — leaving the result inside
            # tmp/ would make it disappear at end-of-build cleanup,
            # breaking the contract "every stage produces a file at its
            # own expected location"
            shutil.copy2(input_path, output_path)
            return output_path
        raise ToolchainExecutionError([formatted_command], result.returncode, result.stderr)

    if not output_path.exists():
        raise ToolchainExecutionError(
            [formatted_command], result.returncode,
            f"the command finished successfully but didn't produce the expected file: {output_path}",
        )

    return output_path


def _execute_stages(
    spec: PipelineSpec,
    source_paths: list[Path],
    table_name: str,
    registry: PluginRegistry,
    config_dict: dict,
    tmp_dir: Path,
    final_out_paths: list[Path],
    cache: BuildCache | None = None,
    source_bytes: bytes | None = None,
    force: bool = False,
) -> list[Path]:
    is_batch = len(source_paths) > 1
    n_stages = len(spec.stages)
    terminal_start = spec.terminal_writer_start()

    start_index = 0
    current_path: Path | None = None if is_batch else source_paths[0]
    current_ir: TableIR | None = None

    if cache is not None and not force and source_bytes is not None:
        resumable = _find_resumable_checkpoint(spec, table_name, source_bytes, config_dict, cache)
        if resumable is not None:
            checkpoint_index, checkpoint_path = resumable
            logger.debug(
                "Resumed from stage checkpoint %d/%d (%d stages skipped): %s",
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
            try:
                if is_batch:
                    current_ir = reader.parse_many(source_paths, config_dict)
                else:
                    current_ir = reader.parse(current_path, config_dict)
            except PayloadError:
                raise
            except Exception as e:
                # A plugin (often a local_plugin just created from the
                # scaffold, never finished) can raise ANY exception —
                # without this, a forgotten 'raise NotImplementedError'
                # in parse()/parse_many() would land raw in front of the
                # user as if it were an internal payload bug.
                raise ReaderParseError(
                    current_path if current_path is not None else Path(f"<batch:{table_name}>"),
                    f"reader '{stage.name}' raised an unexpected error ({type(e).__name__}): {e}",
                    hint="If this is a newly created local plugin, it might still be an incomplete scaffold",
                ) from e
            logger.debug("Parse completed in %.3fs", time.perf_counter() - t0)
            current_path = None
            i += 1

        elif isinstance(stage, WriterStage):
            # A reader feeds the entire group of consecutive writers
            # that follows it with the SAME IR (fan-out) — parse()
            # above has already been called exactly once for the whole
            # group.
            fan_out_size = n_stages - terminal_start
            fan_out_succeeded: list[Path] = []
            fan_out_failures: list[tuple[str, str]] = []
            while i < n_stages and isinstance(spec.stages[i], WriterStage):
                writer_stage = spec.stages[i]
                writer = registry.writers[writer_stage.name]
                in_terminal_group = i >= terminal_start
                # A real fan-out (more than one terminal writer) treats
                # each writer as independent: if one fails, the others
                # are still attempted, otherwise a single faulty writer
                # would hide the fact that the other N-1 were
                # successfully written to disk (see FanOutWriteError,
                # raised only once the group is done). A single or
                # non-terminal writer keeps the previous behavior:
                # it just fails, there's nothing "partial" to save.
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
                except PayloadError as e:
                    if not resilient:
                        raise
                    fan_out_failures.append((writer_stage.name, e.message))
                    i += 1
                    continue
                except Exception as e:
                    # Same reasoning as the reader above: an incomplete/
                    # buggy plugin must never produce a raw traceback,
                    # we wrap it in a clear error.
                    if not resilient:
                        raise WriterEmitError(
                            writer_stage.name,
                            f"unexpected error ({type(e).__name__}): {e}",
                            hint="If this is a newly created local plugin, it might still be an incomplete scaffold",
                        ) from e
                    fan_out_failures.append((writer_stage.name, f"{type(e).__name__}: {e}"))
                    i += 1
                    continue
                logger.debug("Emit completed in %.3fs", time.perf_counter() - t0)

                if in_terminal_group:
                    results.append(emitted)
                    if resilient:
                        fan_out_succeeded.append(emitted)
                else:
                    current_path = emitted
                    # Non-terminal = there's more after it (reader/exec):
                    # a useful checkpoint, covered by the table cache
                    # only if this was the last stage, which it isn't
                    # here.
                    if cache is not None:
                        _persist_stage_checkpoint(
                            spec, table_name, i, emitted, config_dict, source_bytes, cache
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
                    spec, table_name, i, current_path, config_dict, source_bytes, cache
                )
            i += 1

    return results


def build(
    source_paths: list[Path],
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
    table_name: str | None = None,
) -> tuple[list[Path], bool]:
    """Returns (output_paths, was_built). was_built=False if served
    from cache. output_paths has a single element in the common case,
    more than one if the pipeline ends in a fan-out (reader -> several
    writers).

    source_paths: normally a single file (the common case, behavior
    identical in every respect to before); more than one for a batch
    table (see src/payload/docs/BATCH.md) — in that case the resolved
    reader MUST expose parse_many(paths, config), otherwise
    ReaderBatchUnsupportedError. table_name: None derives the stem
    from the (single) source_paths[0], as always; a batch table always
    passes it explicitly, since there's no stem to derive it from.

    cli_opts: one-off overrides from --opt key=value, valid only for
    this invocation. keep_intermediate: doesn't clean up tmp/ after the
    build, useful for inspecting the intermediate files of a
    multi-stage pipeline while debugging."""
    is_batch = len(source_paths) > 1
    missing = next((p for p in source_paths if not p.exists()), None)
    if missing is not None:
        raise SourceNotFoundError(missing)

    table_name = table_name or source_paths[0].stem
    token = current_table.set(table_name)
    try:
        spec = resolve_pipeline_spec(source_paths[0], registry, config, reader_name, writer_name)
        validate_pipeline_against_registry(spec, registry)

        if is_batch:
            reader_stage_name = spec.stages[0].name
            if getattr(registry.readers[reader_stage_name], "parse_many", None) is None:
                raise ReaderBatchUnsupportedError(reader_stage_name)

        config_dict = config.model_dump()
        if cli_opts:
            config_dict["cli_opts"] = cli_opts

        if is_batch:
            source_bytes = None
            named_sources = sorted((p.name, p.read_bytes()) for p in source_paths)
            cache_key = compute_pipeline_cache_key_multi(named_sources, spec.cache_signature(), config_dict)
        else:
            source_bytes = source_paths[0].read_bytes()
            cache_key = compute_pipeline_cache_key(source_bytes, spec.cache_signature(), config_dict)

        out_paths = final_output_paths(spec, table_name, out_dir, registry)

        if not dry_run:
            _clean_stale_outputs(out_dir, table_name, {p.name for p in out_paths})

        if cache is not None and not force and cache.is_fresh(table_name, cache_key):
            logger.info("Cache hit, skip build")
            return out_paths, False

        if dry_run:
            logger.info("[dry-run] pipeline: %s -> %s", describe_pipeline(spec), out_paths)
            return out_paths, True

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_dir / f".tmp_{table_name}" if is_batch else source_paths[0].parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            result_paths = _execute_stages(
                spec, source_paths, table_name, registry, config_dict, tmp_dir, out_paths,
                cache=cache, source_bytes=source_bytes, force=force,
            )
        finally:
            if not keep_intermediate:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if cache is not None:
            cache.update(table_name, cache_key, result_paths)

        logger.info("Build complete: %s -> %s", table_name, result_paths)
        return result_paths, True
    finally:
        current_table.reset(token)
