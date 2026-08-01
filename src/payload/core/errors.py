"""
Exception hierarchy for the tool.

Each exception carries:
- an exit_code consistent with the CLI convention
- a log_level for automatic logging at the central catch point
- an optional hint shown to the user
- structured context (path, plugin name, etc.) useful for logging/debugging

Plugins (reader/writer/doctor check) just raise these exceptions: they
never catch them, log them, or decide the print format. All of that
happens in a single place (cli.run_command), guaranteeing that plugins
written by different people produce errors that look consistent.
"""
from __future__ import annotations

import logging
from pathlib import Path


class PayloadError(Exception):
    """Base of every exception in the tool."""

    exit_code: int = 1
    log_level: int = logging.ERROR

    def __init__(self, message: str, hint: str | None = None, **context):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.context = context

    def to_dict(self) -> dict:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "hint": self.hint,
            **self.context,
        }


# --- Exit code 1: build errors ------------------------------------------

class BuildError(PayloadError):
    exit_code = 1


class ReaderParseError(BuildError):
    """The reader can't make sense of the source."""

    def __init__(self, path: Path, reason: str, **kw):
        super().__init__(f"Error parsing '{path}': {reason}", path=str(path), **kw)


class ToolchainExecutionError(BuildError):
    """An external command (compiler/objcopy/...) failed."""

    def __init__(self, command: list[str], returncode: int, stderr: str = "", **kw):
        super().__init__(
            f"Command '{' '.join(command)}' failed (exit {returncode})",
            hint="Run with -vv to see the full command output",
            command=command,
            returncode=returncode,
            stderr=stderr,
            **kw,
        )


class WriterEmitError(BuildError):
    """The writer can't produce its output."""

    def __init__(self, writer_name: str, reason: str, **kw):
        super().__init__(f"Writer '{writer_name}' can't produce output: {reason}", **kw)


class FanOutWriteError(BuildError):
    """A terminal fan-out (reader -> multiple writers, all fed from the
    same IR) completed only PART of the writers: the failed ones raised
    an error, the successful ones still wrote their own output to disk
    (they're independent of each other). The message explicitly lists
    successes and failures — a generic error would hide the fact that
    2 out of 3 files were actually built, leaving the user with no way
    to know that."""

    def __init__(self, succeeded: list[Path], failures: list[tuple[str, str]], **kw):
        total = len(succeeded) + len(failures)
        ok_desc = ", ".join(p.name for p in succeeded) if succeeded else "none"
        fail_desc = "; ".join(f"'{name}': {reason}" for name, reason in failures)
        super().__init__(
            f"Partial fan-out: {len(succeeded)}/{total} writers completed ({ok_desc}) — failed: {fail_desc}",
            hint="The successful outputs are still on disk: check them before committing an incomplete state",
            succeeded_outputs=[str(p) for p in succeeded],
            failed_writers=[{"writer": name, "reason": reason} for name, reason in failures],
            **kw,
        )


class BatchBuildError(BuildError):
    """Aggregate: one or more tables failed during build-all."""

    def __init__(self, failures: list[PayloadError], **kw):
        super().__init__(
            f"{len(failures)} tables failed out of the whole batch",
            failures=[f.to_dict() for f in failures],
            **kw,
        )


class ReaderBatchUnsupportedError(BuildError):
    """A [[batch_table]] needs a reader that can read N files into a
    single TableIR (Reader.parse_many) — a reader that only exposes
    parse() (single file) can't be used here: no fallback that blindly
    concatenates bytes, which would be wrong for formats that aren't
    line-by-line."""

    def __init__(self, reader_name: str, **kw):
        super().__init__(
            f"Reader '{reader_name}' doesn't support multi-file builds (missing parse_many)",
            hint="See src/payload/docs/BATCH.md — the reader must implement "
                 "parse_many(paths, config) to be usable in a [[batch_table]]",
            reader_name=reader_name,
            **kw,
        )


# --- Exit code 2: config / plugin ---------------------------------------

class ConfigError(PayloadError):
    exit_code = 2


class InvalidConfigError(ConfigError):
    def __init__(self, path: Path, field: str, reason: str, **kw):
        super().__init__(
            f"Invalid config in '{path}': field '{field}' — {reason}",
            hint="Run 'pld doctor' for a full config check",
            path=str(path),
            field=field,
            **kw,
        )


class InvalidPipelineError(ConfigError):
    """Stage-alternation rules violated (reader/writer/exec) — see
    src/payload/docs/PIPELINE.md. Raised BEFORE running any stage."""

    def __init__(self, stage_index: int, reason: str, **kw):
        super().__init__(
            f"Invalid pipeline at stage #{stage_index}: {reason}",
            hint="See src/payload/docs/PIPELINE.md for the reader/writer/exec alternation rules",
            stage_index=stage_index,
            **kw,
        )


class PluginError(ConfigError):
    pass


class PluginLoadError(PluginError):
    def __init__(self, plugin_name: str, group: str, reason: str, **kw):
        super().__init__(
            f"Plugin '{plugin_name}' ({group}) can't be loaded: {reason}",
            hint="Check that the package is installed correctly",
            plugin_name=plugin_name,
            group=group,
            **kw,
        )


class MissingPluginDependenciesError(PluginError):
    def __init__(self, path: Path, missing: list[str], **kw):
        super().__init__(
            f"Local plugin '{path.name}': missing dependencies: {', '.join(missing)}",
            hint=f"Run 'pld plugin install-deps {path}' to install them",
            path=str(path), missing=missing, **kw,
        )


class PluginApiVersionError(PluginError):
    """The plugin declares an API version incompatible with the core."""

    def __init__(self, plugin_name: str, plugin_api_version: str, core_api_version: str, **kw):
        super().__init__(
            f"Plugin '{plugin_name}' requires API v{plugin_api_version}, "
            f"the core exposes v{core_api_version}",
            hint="Update the plugin or the core so the versions match",
            plugin_name=plugin_name,
            plugin_api_version=plugin_api_version,
            core_api_version=core_api_version,
            **kw,
        )


class AmbiguousReaderError(PluginError):
    def __init__(self, path: Path, candidates: list[str], **kw):
        super().__init__(
            f"Multiple candidate readers for '{path}': {', '.join(candidates)}",
            hint="Specify explicitly with --from <reader>",
            path=str(path),
            candidates=candidates,
            **kw,
        )


class WriterNotSpecifiedError(ConfigError):
    """No writer could be resolved: neither --to, nor config, nor a
    default suggested by the reader."""

    def __init__(self, path: Path, reader_name: str, **kw):
        super().__init__(
            f"No writer specified for '{path}' (reader '{reader_name}' "
            f"doesn't suggest a default)",
            hint="Pass --to <writer>, set 'defaults.writer' in config, "
                 "or use a reader that declares a default_writer",
            path=str(path), reader_name=reader_name, **kw,
        )


class InvalidCliOptionError(ConfigError):
    def __init__(self, raw: str, **kw):
        super().__init__(
            f"Invalid option: '{raw}'",
            hint="Expected format: --opt key=value",
            raw=raw, **kw,
        )


class BatchTableError(ConfigError):
    """A [[batch_table]] doesn't resolve into a valid set of files:
    'sources' empty after glob expansion, a literal path missing from
    disk, or two resolved files sharing the same filename (silent
    collision in source_blobs, keyed by filename)."""

    def __init__(self, batch_name: str, reason: str, **kw):
        super().__init__(
            f"Batch table '{batch_name}' is invalid: {reason}",
            # '\\[\\[' not '[[': this text goes through a rich.Console
            # with markup=True (run_command in cli.py) — a literal
            # '[[batch_table]]' would be parsed as the start of a tag
            # and printed as '[]', the brackets need escaping.
            hint="See src/payload/docs/BATCH.md for the \\[\\[batch_table]] schema",
            batch_name=batch_name,
            **kw,
        )


class DuplicateTableNameError(ConfigError):
    """Two or more sources share the same table name (filename stem):
    build output, golden, and history are all indexed by name, so a
    collision would silently overwrite one with the other."""

    def __init__(self, duplicates: dict, **kw):
        lines = [f"'{name}': {', '.join(str(p) for p in paths)}" for name, paths in duplicates.items()]
        super().__init__(
            f"{len(duplicates)} duplicate table names",
            hint="Rename the files: table names must be unique across the whole "
                 "project (used for build/golden/history)\n" + "\n".join(lines),
            duplicates={name: [str(p) for p in paths] for name, paths in duplicates.items()},
            **kw,
        )


class InvalidImportError(ConfigError):
    """Invalid filename for an import (path traversal, directory
    separators, empty/hidden name) — this value comes from the
    user/web client, it's never trusted as-is before joining it to the
    project root."""

    def __init__(self, filename: str, **kw):
        super().__init__(f"Invalid filename for import: '{filename}'", filename=filename, **kw)


class EmptySourceError(ConfigError):
    """The uploaded file is 0 bytes — an empty table would still build
    fine (it's not a pipeline error, there's simply nothing to read),
    but it makes no sense as an import: it's almost always a file
    picked by mistake, or an interrupted upload. Rejected BEFORE
    writing anything, same spirit as the reader check in
    import_single_table."""

    def __init__(self, filename: str, **kw):
        super().__init__(f"File '{filename}' is empty (0 bytes): rejected", filename=filename, **kw)


class TableAlreadyExistsError(ConfigError):
    """Importing an external file would reuse a table name that
    already exists in the project (single file or [[batch_table]]) —
    without explicit consent to overwrite, the import refuses instead
    of silently replacing the tracked source."""

    def __init__(self, name: str, **kw):
        super().__init__(
            f"A table named '{name}' already exists in this project",
            hint="Pass explicit overwrite confirmation if you want to replace its content",
            name=name,
            **kw,
        )


# --- Exit code 3: golden --------------------------------------------------

class GoldenError(PayloadError):
    exit_code = 3


class GoldenMismatchError(GoldenError):
    """Source unchanged since the golden snapshot, but the current
    output has different bytes — a real regression, exactly the case
    golden exists for."""

    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"The output of '{table_name}' doesn't match the golden snapshot",
            hint=(
                "Run 'pld golden diff <table>' to see the differences, "
                "or 'pld commit --golden' if the change is intentional"
            ),
            table=table_name,
            **kw,
        )


class GoldenStaleError(GoldenError):
    """The SOURCE changed since the golden snapshot — comparing the
    output is no longer reliable (it might match by chance, or not).
    A recommit is needed first to know whether the new output is still
    correct."""

    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"The source of '{table_name}' changed after the last golden: the comparison isn't reliable",
            hint="Run 'pld commit --golden' to update golden to the current state, if that's the correct one",
            table=table_name,
            **kw,
        )


class GoldenMissingError(GoldenError):
    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"No golden set for '{table_name}'",
            hint="Run 'pld golden set <table>' to set it (requires at least one saved snapshot)",
            table=table_name,
            **kw,
        )


# --- Exit code 4: file / reader / writer not found -----------------------

class NotFoundError(PayloadError):
    exit_code = 4


class SourceNotFoundError(NotFoundError):
    def __init__(self, path: Path, **kw):
        super().__init__(f"Source file not found: '{path}'", path=str(path), **kw)


class ProjectNotInitializedError(NotFoundError):
    """Raised by every command that operates on a project (build,
    status, commit, etc.) when 'root' has no table-tool.toml — same
    spirit as 'git' outside a repository: fail immediately with a
    clear message instead of silently falling back to defaults."""

    def __init__(self, root: Path, **kw):
        super().__init__(
            f"'{root}' is not an initialized payload project",
            hint="Run 'pld init' in this folder, or 'pld init <name>' to create a new one",
            path=str(root),
            **kw,
        )


class NoReaderFoundError(NotFoundError):
    def __init__(self, path: Path, **kw):
        super().__init__(
            f"No reader found for '{path}'",
            hint="Use 'pld plugins list' to see the supported formats",
            path=str(path),
            **kw,
        )


class NoWriterFoundError(NotFoundError):
    def __init__(self, name: str, **kw):
        super().__init__(
            f"Writer '{name}' not registered",
            hint="Use 'pld plugins list' to see the available writers",
            name=name,
            **kw,
        )


class TableNotFoundError(NotFoundError):
    """No source discovered with this table name (stem) — used by
    commands/routes that look up a table by name instead of an
    explicit path (e.g. 'pld config show <table>', the web routes)."""

    def __init__(self, name: str, **kw):
        super().__init__(f"Table '{name}' not found", name=name, **kw)


class PluginNotFoundError(NotFoundError):
    """No reader/writer/doctor-check registered with this name — used
    by commands/routes that look up a plugin by name (e.g. 'pld plugin
    info <name>', the web routes)."""

    def __init__(self, name: str, **kw):
        super().__init__(
            f"Plugin '{name}' not found",
            hint="Use 'pld plugins' to see the available ones",
            name=name,
            **kw,
        )


# --- Exit code 5: history ---------------------------------------------------

class HistoryError(PayloadError):
    exit_code = 5


class SnapshotNotFoundError(HistoryError):
    def __init__(self, target, reason: str, **kw):
        super().__init__(
            f"Snapshot not found for '{target}': {reason}",
            hint="Use 'pld log <table>' to see the available snapshots",
            target=str(target),
            **kw,
        )


class NothingToCommitError(HistoryError):
    log_level = logging.INFO

    def __init__(self, **kw):
        super().__init__(
            "No changed table to save",
            hint="Use 'pld status' to see the current state",
            **kw,
        )


class NoOutputToCommitError(HistoryError):
    """'commit' never builds on its own — it commits the source plus
    whatever output is already in output_dir. If that output is
    completely missing (zero files, not a partial fan-out with only
    some writers missing), the resulting snapshot would have no output
    attached: not very useful (nothing to compare against golden, no
    record of what that build actually produced), and almost always a
    sign of having forgotten 'pld build' before committing — different
    from the partial fan-out case (BuildError->FanOutWriteError still
    committed), which stays allowed with just a warning."""

    def __init__(self, table_names: list, **kw):
        names = ", ".join(table_names)
        super().__init__(
            f"No output found for: {names} — nothing to attach to the snapshot",
            hint="Run 'pld build' (or 'pld build-all') before 'pld commit'",
            table_names=list(table_names),
            **kw,
        )


class TableNotTrackedError(HistoryError):
    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"'{table_name}' has never been saved with 'pld commit'",
            table_name=table_name,
            **kw,
        )
