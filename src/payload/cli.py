"""
payload (pld) CLI. A single exception-catching point (run_command)
decides exit code and print format: individual commands stay clean and
only raise exceptions from the payload.core.errors hierarchy.
"""
from __future__ import annotations

import random
import shutil
import subprocess
import sys
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from payload.core.batch import run_batch_build
from payload.core.batch_tables import effective_config, resolve_batch_tables
from payload.core.cache import BuildCache
from payload.core.config import GLOBAL_CONFIG_FILENAME, load_config, resolve_config_with_provenance
from payload.core.discovery import (
    TableRef,
    all_table_refs,
    check_no_batch_name_collisions,
    discover_for_history,
    discover_table_sources,
    exclude_batch_members,
    find_duplicate_stems,
    resolve_table_ref,
)
from payload.core.doctor import run_doctor
from payload.core.errors import (
    BatchBuildError,
    DuplicateTableNameError,
    GoldenMismatchError,
    GoldenStaleError,
    InvalidCliOptionError,
    NoOutputToCommitError,
    NothingToCommitError,
    PayloadError,
    ProjectNotInitializedError,
    SourceNotFoundError,
)
from payload.core.golden import check_golden, clear_golden, golden_diff, set_golden
from payload.core.history import HistoryStore, legacy_compatible_source_blobs
from payload.core.logging_setup import setup_logging
from payload.core.pipeline import build, describe_table_build
from payload.core.plugin_base import CheckStatus
from payload.core.registry import load_plugins
from payload.core.table_admin import (
    delete_batch_member,
    delete_table,
    import_batch_member,
    import_new_batch_table,
    import_single_table,
)
from payload._version import __version__
from payload.init_cmd import init_project, is_nonempty_existing_dir
from payload.plugin_scaffold import scaffold_local_plugin, scaffold_plugin
from payload.ui.banner import print_banner, random_tip
from payload.ui.flavor import random_loading_phrase
from payload.watch import watch as watch_loop

app = typer.Typer(name="pld", help="payload — table management for embedded systems")
golden_app = typer.Typer(help="Golden file management")
plugin_app = typer.Typer(help="Plugin management/scaffolding")
config_app = typer.Typer(help="Resolved config inspection")
pipeline_app = typer.Typer(help="Pipeline inspection")
app.add_typer(golden_app, name="golden")
app.add_typer(plugin_app, name="plugin")
app.add_typer(config_app, name="config")
app.add_typer(pipeline_app, name="pipeline")

# Windows consoles with a legacy codepage (cp1252/'charmap', not UTF-8)
# can't represent emoji like 💡 used in tips — without this, a
# UnicodeEncodeError while writing crashes the whole command over a
# purely cosmetic detail. errors="replace" substitutes the
# unrepresentable character with a placeholder instead of raising, and
# has no effect when the stream already supports UTF-8 (the common
# case on Linux/macOS and modern Windows Terminal). Must happen BEFORE
# creating the Console instances below, otherwise rich might have
# already read the stream's original encoding.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:  # pragma: no cover - purely cosmetic, must NEVER block startup
            # 'pytest' (and other contexts that replace sys.stdout/stderr
            # with custom wrappers, e.g. output capture) can expose a
            # 'reconfigure' attribute that behaves differently from a
            # real io.TextIOWrapper and raise unexpected exceptions — a
            # narrow except on ValueError/OSError didn't cover all of
            # them, breaking the module IMPORT (and so every test).
            pass

console = Console()
err_console = Console(stderr=True)

STATUS_STYLE = {CheckStatus.OK: "green", CheckStatus.WARN: "yellow", CheckStatus.FAIL: "red"}
STATUS_ICON = {CheckStatus.OK: "✓", CheckStatus.WARN: "!", CheckStatus.FAIL: "✗"}


def _version_callback(value: bool):
    if value:
        console.print(f"payload (pld) v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    verbose: int = typer.Option(0, "-v", count=True, help="Increase verbosity (-v, -vv)"),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the version and exit",
    ),
):
    ctx.ensure_object(dict)
    ctx.obj["verbosity"] = verbose
    setup_logging(verbose)


def run_command(fn, verbosity: int):
    """Single catch point: decides exit code and print format for
    every PayloadError. Internal bugs (unexpected exceptions) stay
    distinct and always show a full traceback.

    NOTE: err_console.print is the ONLY display channel for a
    PayloadError — no parallel logging via the 'payload' logger (which
    has a RichHandler attached to the same console, see
    logging_setup.py): a logger.log(e.log_level, ...) call used to be
    here too, duplicating the same message on screen (once as an
    'ERROR ...' line from the RichHandler, once as '✗ ...' from here) —
    a bug that stayed invisible until tests exercised it only via
    CliRunner/capsys.

    NOTE 2: typer.Exit must be re-raised WITHOUT going through the
    generic 'Exception' branch. It's the exception typer uses to
    implement a clean exit (used by several commands with
    'raise typer.Exit(code=...)' after already printing a clear
    message) — if it isn't caught explicitly before the generic
    'except Exception', a controlled exit (e.g. 'doctor' with failed
    checks) gets mistaken for a tool crash, complete with a misleading
    traceback shown to the user."""
    try:
        return fn()
    except PayloadError as e:
        err_console.print(f"[red]✗[/] {e.message}")
        if e.hint:
            err_console.print(f"    → {e.hint}", style="dim")
        if verbosity >= 2:
            stderr_text = e.context.get("stderr")
            stdout_text = e.context.get("stdout")
            if stderr_text:
                err_console.print("\n[bold]--- command stderr ---[/]")
                err_console.print(stderr_text)
            if stdout_text:
                err_console.print("\n[bold]--- command stdout ---[/]")
                err_console.print(stdout_text)
        raise typer.Exit(code=e.exit_code)
    except typer.Exit:
        raise
    except Exception as e:  # a bug in the tool, not an "expected" error
        err_console.print(f"[red]✗ Unexpected internal error:[/] {e}")
        err_console.print_exception()
        raise typer.Exit(code=1)


def require_project_root(root: Path) -> None:
    """Like 'git' outside a repository: commands that operate on a
    project (build, status, commit, golden, etc.) require that 'root'
    has already been initialized with 'pld init'. Commands that don't
    depend on a specific project (init, view, plugin
    validate/new/new-local, plugins/plugin info) don't call this
    function."""
    if not (root / GLOBAL_CONFIG_FILENAME).is_file():
        raise ProjectNotInitializedError(root)


# --------------------------------------------------------------------------
# build / build-all
# --------------------------------------------------------------------------

def _parse_opts(raw_opts: Optional[list[str]]) -> dict:
    """Converts a list of 'key=value' (from repeated --opt) into a
    dict. One-off overrides for this invocation, read by plugins via
    config.get("cli_opts", {}).get("key") — never persisted to any
    file."""
    result: dict = {}
    for raw in raw_opts or []:
        if "=" not in raw:
            raise InvalidCliOptionError(raw)
        key, _, value = raw.partition("=")
        if not key:
            raise InvalidCliOptionError(raw)
        result[key] = value
    return result


@app.command(name="build")
def build_cmd(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Table source file, or the name of a [[batch_table]]"),
    from_: Optional[str] = typer.Option(None, "--from", help="Explicit reader"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer to use"),
    out: Path = typer.Option(Path("build"), "--out", help="Output directory"),
    force: bool = typer.Option(False, "--force", help="Bypass the cache"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without executing"),
    check_golden_flag: bool = typer.Option(
        False, "--check-golden", help="Fail if the output doesn't match golden"
    ),
    opt: Optional[list[str]] = typer.Option(
        None, "--opt", help="key=value override for the active plugin, e.g. --opt delimiter=; (repeatable)"
    ),
    keep_intermediate: bool = typer.Option(
        False, "--keep-intermediate", help="Don't clean up tmp/ after the build (multi-stage pipeline debugging)"
    ),
):
    """Builds a single table — a source file, or the name of a batch
    table declared in [[batch_table]] (see src/payload/docs/BATCH.md),
    which has no single file to pass."""

    def _run():
        root = Path.cwd()
        require_project_root(root)
        registry = load_plugins()
        source_path = Path(source)

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
            config = effective_config(base_config, batch)
            table_name = batch.name

        cache = BuildCache(Path(config.defaults.cache_dir))
        cli_opts = _parse_opts(opt)

        with console.status(f"[cyan]{random_loading_phrase()}[/]", spinner="dots"):
            out_paths, was_built = build(
                source_paths, registry, config, out, cache=cache,
                reader_name=from_, writer_name=to, force=force, dry_run=dry_run,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate, table_name=table_name,
            )
            cache.save()

            if check_golden_flag and not dry_run:
                history = HistoryStore(Path.cwd())
                result = check_golden(history, table_name, source_paths, out_paths)
                if result.status == "mismatch":
                    raise GoldenMismatchError(table_name)
                if result.status == "stale":
                    raise GoldenStaleError(table_name)

        status = "built" if was_built else "from cache"
        destinations = ", ".join(str(p) for p in out_paths)
        console.print(f"[green]✓[/] {table_name} → {destinations} ({status})")

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="build-all")
def build_all_cmd(
    ctx: typer.Context,
    root: Path = typer.Argument(Path("."), help="Root folder to scan"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer to use"),
    out: Path = typer.Option(Path("build"), "--out", help="Output directory"),
    jobs: int = typer.Option(1, "--jobs", help="Parallelism level"),
    filter_glob: Optional[str] = typer.Option(None, "--filter", help="Glob to filter sources"),
    force: bool = typer.Option(False, "--force"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    check_golden_flag: bool = typer.Option(False, "--check-golden"),
    opt: Optional[list[str]] = typer.Option(
        None, "--opt", help="key=value override for the active plugin, applied to every table (repeatable)"
    ),
    keep_intermediate: bool = typer.Option(
        False, "--keep-intermediate", help="Don't clean up tmp/ after each build (multi-stage pipeline debugging)"
    ),
):
    """Recursive batch build over every table found under root."""

    def _run():
        require_project_root(root)
        registry = load_plugins(project_root=root)
        base_config = load_config(root)
        cache = BuildCache(Path(base_config.defaults.cache_dir))
        cli_opts = _parse_opts(opt)

        known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
        sources = discover_table_sources(
            root, known_ext, Path(base_config.defaults.output_dir), filter_glob
        )

        duplicates = find_duplicate_stems(sources)
        if duplicates:
            raise DuplicateTableNameError(duplicates)

        # batch tables aren't filtered by --filter (it filters files on
        # disk by path, batch tables are declared by name in config) —
        # always included in full.
        batch_tables = resolve_batch_tables(root, base_config)
        sources = exclude_batch_members(sources, batch_tables)
        check_no_batch_name_collisions(sources, batch_tables)
        tables = all_table_refs(sources, batch_tables)

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(random_loading_phrase(), total=len(tables))

            def _on_result(ref: TableRef, status: str) -> None:
                progress.update(task, description=f"[cyan]{ref.name}[/]")
                progress.advance(task)

            # jobs=1 -> same sequential behavior as before, no thread
            # pool overhead. jobs>1 -> parallelizes, since tables are
            # independent of each other (no cross references).
            summary = run_batch_build(
                tables, root, registry, cache, out, jobs=jobs, writer_name=to,
                force=force, dry_run=dry_run, check_golden_flag=check_golden_flag,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate,
                on_table_result=_on_result,
            )

        summary_style = "red" if summary.errors else ("yellow" if summary.golden_mismatch else "green")
        console.print(
            Panel(
                f"[green]{summary.built}[/] built   "
                f"[cyan]{summary.cached}[/] from cache   "
                f"[yellow]{summary.golden_mismatch}[/] golden mismatch   "
                f"[red]{summary.errors}[/] errors",
                title=f"{len(tables)} tables processed",
                border_style=summary_style,
            )
        )
        if random.random() < 0.3:
            console.print(f"[dim]💡 {random_tip()}[/]")

        if summary.failures:
            raise BatchBuildError(summary.failures)

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# watch
# --------------------------------------------------------------------------

@app.command()
def watch(
    ctx: typer.Context,
    root: Path = typer.Argument(Path("."), help="File or folder to watch"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer to use"),
    out: Path = typer.Option(Path("build"), "--out", help="Output directory"),
    jobs: int = typer.Option(1, "--jobs", help="Parallelism level for the initial build"),
    filter_glob: Optional[str] = typer.Option(
        None, "--filter", help="Glob to filter sources in the initial build (not in live watch)"
    ),
):
    """Initial build of every table under 'root', then automatic
    rebuild on every save (Ctrl+C to exit)."""

    def _run():
        project_root = Path.cwd()
        require_project_root(project_root)
        registry = load_plugins(project_root=project_root)
        config = load_config(project_root)
        cache = BuildCache(Path(config.defaults.cache_dir))
        known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
        watch_root = root if root.is_dir() else root.parent

        # The initial build must never prevent watch from starting —
        # same philosophy as payload/watch.py, which never dies from a
        # build error while watching live.
        batch_member_paths: set[Path] = set()
        try:
            sources = discover_table_sources(root, known_ext, Path(config.defaults.output_dir), filter_glob)
            duplicates = find_duplicate_stems(sources)
            if duplicates:
                raise DuplicateTableNameError(duplicates)
            batch_tables = resolve_batch_tables(root, config)
            batch_member_paths = {p.resolve() for bt in batch_tables for p in bt.source_paths}
            sources = exclude_batch_members(sources, batch_tables)
            check_no_batch_name_collisions(sources, batch_tables)
            tables = all_table_refs(sources, batch_tables)

            summary = run_batch_build(tables, root, registry, cache, out, jobs=jobs, writer_name=to)
            if summary.failures:
                console.print(
                    f"[yellow]![/] initial build: {len(summary.failures)}/{len(tables)} "
                    "tables failed — continuing with watch anyway"
                )
            else:
                console.print(
                    f"[green]✓[/] initial build: {summary.built} built, "
                    f"{summary.cached} from cache ({len(tables)} tables)"
                )
        except PayloadError as e:
            console.print(f"[yellow]![/] initial build failed ({e.message}) — continuing with watch anyway")

        def on_change(src: Path):
            if src.resolve() in batch_member_paths:
                # part of a [[batch_table]]: rebuilding it as if it
                # were a standalone (single-file) table would produce a
                # wrong/duplicate output — live-reload for batch tables
                # isn't supported (see BATCH.md), 'pld build
                # <batch_name>' remains the way to update it.
                console.print(
                    f"[dim]— {src.name} is part of a batch table: automatic rebuild "
                    "not supported in watch, use 'pld build <batch_name>'[/]"
                )
                return
            per_table_config = load_config(project_root, source_path=src)
            out_paths, was_built = build(
                [src], registry, per_table_config, out, cache=cache, writer_name=to,
            )
            cache.save()
            status = "rebuilt" if was_built else "unchanged (cache)"
            destinations = ", ".join(str(p) for p in out_paths)
            console.print(f"[green]✓[/] {src.name} → {destinations} ({status})")

        watch_loop(watch_root, known_ext, out, on_change)

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# view
# --------------------------------------------------------------------------

@app.command()
def view(
    ctx: typer.Context,
    source: Path = typer.Argument(...),
    from_: Optional[str] = typer.Option(None, "--from"),
):
    """Displays the raw content (bytes + comments) of a table."""

    def _run():
        registry = load_plugins()
        reader = registry.find_reader(source, from_)
        ir = reader.parse(source, {})

        table = Table(title=f"{ir.name} ({len(ir.data)} bytes)")
        table.add_column("Offset", style="dim")
        table.add_column("Bytes")
        table.add_column("Comment", style="italic")

        comments_by_offset = dict(ir.comments)
        for i in range(0, len(ir.data), 8):
            chunk = ir.data[i:i + 8]
            hex_str = " ".join(f"{b:02X}" for b in chunk)
            comment = comments_by_offset.get(i, "")
            table.add_row(f"0x{i:04X}", hex_str, comment)

        console.print(table)

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# status / commit / log / diff / restore  (lightweight per-table checkpoint)
# --------------------------------------------------------------------------

@app.command()
def status(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Shows which tables changed since the last snapshot."""

    def _run():
        require_project_root(root)
        sources, batch_tables, config = discover_for_history(root)
        history = HistoryStore(root)
        output_dir = Path(config.defaults.output_dir)
        tables = all_table_refs(sources, batch_tables)

        table = Table(title="Table status")
        table.add_column("Table")
        table.add_column("Status")

        any_change = False
        for ref in tables:
            display = f"{ref.name} [dim](batch, {len(ref.source_paths)} files)[/]" if ref.is_batch else ref.name
            output_paths = list(output_dir.glob(f"{ref.name}.*")) if output_dir.exists() else []
            last = history.last_snapshot(ref.name)
            if last is None:
                table.add_row(display, "[yellow]never saved[/]")
                any_change = True
            elif history.is_dirty(ref.name, ref.source_paths, output_paths):
                table.add_row(display, "[red]changed[/]")
                any_change = True
            else:
                table.add_row(display, "[green]unchanged[/]")

        console.print(table)
        if tables and not any_change:
            console.print("[dim]No changes to save.[/]")
        elif not tables:
            console.print("[dim]No table found under this folder.[/]")

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def commit(
    ctx: typer.Context,
    message: str = typer.Option(..., "-m", "--message", help="Snapshot message"),
    only: Optional[list[str]] = typer.Option(
        None, "--only", help="Limit to these table names (repeatable, e.g. --only t1 --only t2)"
    ),
    golden: bool = typer.Option(
        False, "--golden", help="Also set the new snapshot as golden for every committed table"
    ),
    root: Path = typer.Argument(Path(".")),
):
    """Saves a snapshot of source + generated output for every changed
    table (or only the ones given with --only)."""

    def _run():
        require_project_root(root)
        sources, batch_tables, config = discover_for_history(root)
        history = HistoryStore(root)
        registry = load_plugins(project_root=root)
        output_dir = Path(config.defaults.output_dir)
        tables = all_table_refs(sources, batch_tables)

        if only:
            tables = [t for t in tables if t.name in only]

        dirty = []
        for ref in tables:
            output_paths = list(output_dir.glob(f"{ref.name}.*"))
            if history.is_dirty(ref.name, ref.source_paths, output_paths):
                table_config = effective_config(config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
                build_info = describe_table_build(
                    ref.source_paths, registry, table_config, output_paths, output_dir, table_name=ref.name,
                )
                dirty.append((ref, output_paths, build_info))
        if not dirty:
            raise NothingToCommitError()

        # zero output (not a PARTIAL fan-out, which stays allowed with
        # just a warning) is almost always "forgot to build first" —
        # that table gets skipped instead of committing a useless
        # snapshot, but without failing the whole command if AT LEAST
        # one other table has valid output.
        blocked = [ref.name for ref, output_paths, build_info in dirty if not output_paths and build_info["missing_outputs"]]
        committable = [d for d in dirty if d[0].name not in blocked]
        if not committable:
            raise NoOutputToCommitError(blocked)

        for ref, output_paths, build_info in committable:
            snap = history.commit(ref.name, ref.source_paths, output_paths, message, **build_info)
            n_out = len(snap.output_blobs)
            suffix = ""
            if golden:
                history.set_golden(ref.name, snap.id)
                suffix = " [gold1]★ golden[/]"
            console.print(f"[green]✓[/] {ref.name} → snapshot #{snap.id} ({n_out} outputs attached){suffix}")
            if snap.missing_outputs:
                console.print(
                    f"    [yellow]![/] incomplete pipeline: missing {', '.join(snap.missing_outputs)} "
                    "(a writer in the group produced no output — verify before trusting this snapshot)"
                )
        for name in blocked:
            console.print(f"[yellow]![/] {name}: skipped, no output found — run 'pld build' first")

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="log")
def log_cmd(
    ctx: typer.Context,
    table_name: Optional[str] = typer.Argument(None, help="If omitted, shows every tracked table"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Snapshot history, like 'git log'."""

    def _run():
        require_project_root(root)
        history = HistoryStore(root)
        names = [table_name] if table_name else history.all_tracked_tables()

        if not names:
            console.print("No table tracked yet. Use [bold]pld commit -m \"...\"[/] to start.")
            return

        for name in names:
            snapshots = history.log(name)
            if not snapshots:
                continue
            head_id = history.head_snapshot_id(name)
            console.print(f"[bold]{name}[/]")
            for s in reversed(snapshots):
                outputs = ", ".join(s.output_blobs) if s.output_blobs else "—"
                if s.pipeline_explicit and s.pipeline_description:
                    pipeline_str = f"  [dim]{s.pipeline_description}[/]"
                else:
                    pipeline_bits = [s.reader] if s.reader else []
                    if s.writers:
                        pipeline_bits.append("+".join(s.writers))
                    pipeline_str = f"  [dim]{' → '.join(pipeline_bits)}[/]" if pipeline_bits else ""
                marker = "  [cyan]● current[/]" if s.id == head_id else ""
                warn = f"  [yellow]![/] incomplete pipeline, missing {', '.join(s.missing_outputs)}" if s.missing_outputs else ""
                console.print(
                    f"  #{s.id}  {s.timestamp}  {s.message}  [dim]({outputs})[/]{pipeline_str}{marker}{warn}"
                )

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="diff")
def diff_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot: Optional[int] = typer.Option(None, "--snapshot", help="Snapshot ID to compare (default: latest)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Compares the current source against a saved snapshot."""

    def _run():
        require_project_root(root)
        history = HistoryStore(root)
        snap_id = snapshot
        if snap_id is None:
            last = history.last_snapshot(table_name)
            if last is None:
                console.print(f"[yellow]![/] no snapshot for '{table_name}'")
                raise typer.Exit(code=5)
            snap_id = last.id

        snap = history.get_snapshot(table_name, snap_id)

        sources, batch_tables, _ = discover_for_history(root)
        ref = resolve_table_ref(sources, batch_tables, table_name)
        if ref is None:
            console.print(f"[red]✗[/] source for '{table_name}' not found")
            raise typer.Exit(code=4)

        comparable_blobs = legacy_compatible_source_blobs(ref.source_paths, snap.source_blobs)
        any_diff = False
        for src in ref.source_paths:
            current = src.read_bytes()
            blob_hash = comparable_blobs.get(src.name)
            expected = history.read_blob(blob_hash) if blob_hash else b""
            if current == expected:
                continue
            any_diff = True

            console.print(f"[bold]Diff {src.name} vs snapshot #{snap_id}[/]")
            max_len = max(len(current), len(expected))
            for i in range(0, max_len, 8):
                c_chunk = current[i:i + 8]
                e_chunk = expected[i:i + 8]
                if c_chunk != e_chunk:
                    console.print(
                        f"0x{i:04X}  current: {c_chunk.hex(' ')}  |  snapshot: {e_chunk.hex(' ')}",
                        style="red",
                    )

        if not any_diff:
            console.print(f"No difference from snapshot #{snap_id}")

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="restore")
def restore_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot_id: Optional[int] = typer.Argument(
        None, help="ID of the snapshot to go back to (see 'pld log'); omitted = the latest"
    ),
    root: Path = typer.Option(Path("."), "--root"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
):
    """Restores both the source AND the generated output to the state
    of a previous snapshot (the latest, if not specified — useful to
    undo an accidental 'pld rm' without first checking 'pld log'). If
    the source is no longer on disk (e.g. deleted with 'pld rm' or by
    hand) and it's a single-file table, it's recreated from scratch at
    the location it lived in at commit time — deleted batch tables
    aren't restorable this way (see src/payload/docs/BATCH.md)."""

    def _run():
        require_project_root(root)
        history = HistoryStore(root)
        resolved_snapshot_id = snapshot_id if snapshot_id is not None else history.head_snapshot_id(table_name)
        if resolved_snapshot_id is None:
            console.print(f"[red]✗[/] '{table_name}' has no snapshot")
            raise typer.Exit(code=5)
        snapshot = history.get_snapshot(table_name, resolved_snapshot_id)  # validates it exists, raises if missing

        sources, batch_tables, config = discover_for_history(root)
        ref = resolve_table_ref(sources, batch_tables, table_name)

        recreating = False
        if ref is not None:
            source_paths = ref.source_paths
        else:
            if len(snapshot.source_blobs) != 1:
                console.print(
                    f"[red]✗[/] '{table_name}' is not on disk and can't be restored automatically "
                    "(it was a batch table — see src/payload/docs/BATCH.md)"
                )
                raise typer.Exit(code=4)
            source_paths = history.source_paths_for_snapshot(table_name, resolved_snapshot_id)
            recreating = True

        if not yes:
            names = ", ".join(p.name for p in source_paths)
            verb = "recreated" if recreating else "overwritten"
            console.print(
                f"{names} and its outputs will be {verb} "
                f"with the state of snapshot #{resolved_snapshot_id}. "
                "No new snapshot is created: only the current pointer moves back, "
                "the history stays intact."
            )
            if not typer.confirm("Confirm?"):
                console.print("Cancelled.")
                return

        result = history.restore(table_name, resolved_snapshot_id, source_paths, Path(config.defaults.output_dir))
        for w in result.written:
            console.print(f"[green]✓[/] restored {w}")
        for r in result.removed:
            console.print(f"[yellow]—[/] removed (not part of snapshot #{resolved_snapshot_id}): {r}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# rm
# --------------------------------------------------------------------------

@app.command(name="rm")
def rm_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(..., help="Name of the table (or of a [[batch_table]]) to delete"),
    member: Optional[str] = typer.Option(
        None, "--member", help="Delete only this member file of a batch table, not the whole table"
    ),
    force: bool = typer.Option(
        False, "--force", help="Required to run: without it, the command refuses (safety net against a name typo)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation (for scripts/CI)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Deletes the source(s) + output + cache of a table (or of one of
    its batch members, with --member) — NEVER touches history: the
    snapshots stay browsable with 'pld log', and for a single-file
    table also restorable with 'pld restore' (see 'pld restore
    --help'). For a batch table without --member, deletes every member
    AND its [[batch_table]] entry from table-tool.toml."""

    def _run():
        require_project_root(root)
        if not force:
            console.print("[red]✗[/] 'pld rm' requires --force: it's a destructive operation")
            raise typer.Exit(code=2)

        sources, batch_tables, config = discover_for_history(root)
        ref = _resolve_ref_or_exit(sources, batch_tables, table_name)
        table_config = effective_config(config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
        output_dir = Path(table_config.defaults.output_dir)
        cache = BuildCache(Path(table_config.defaults.cache_dir))
        history = HistoryStore(root)

        if member is not None:
            if not ref.is_batch:
                console.print("[red]✗[/] '--member' only applies to a batch table")
                raise typer.Exit(code=2)
            if not any(p.name == member for p in ref.source_paths):
                console.print(f"[red]✗[/] '{member}' is not a member of '{table_name}'")
                raise typer.Exit(code=4)
            if not yes:
                console.print(f"Member file '{member}' of '{table_name}' will be deleted (history stays intact).")
                if not typer.confirm("Confirm?"):
                    console.print("Cancelled.")
                    return
            result = delete_batch_member(root, ref.batch, member, output_dir, cache)
        else:
            if not yes:
                kind = "batch table (all members)" if ref.is_batch else "table"
                console.print(f"The following will be deleted: source(s) of {kind} '{table_name}' and its output in {output_dir}.")
                console.print("[dim]History stays intact and browsable with 'pld log'.[/]")
                out_paths = list(output_dir.glob(f"{ref.name}.*")) if output_dir.exists() else []
                if history.is_dirty(ref.name, ref.source_paths, out_paths):
                    console.print("[yellow]![/] this table has uncommitted changes: they will be lost forever.")
                if ref.is_batch:
                    console.print(f"[dim]— the \\[\\[batch_table]] '{table_name}' will also be removed from table-tool.toml[/]")
                if not typer.confirm("Confirm?"):
                    console.print("Cancelled.")
                    return
            result = delete_table(root, ref, output_dir, cache)

        cache.save()
        for p in result.removed_sources:
            console.print(f"[green]✓[/] removed {p}")
        for p in result.removed_outputs:
            console.print(f"[green]✓[/] removed {p}")
        if result.batch_entry_removed:
            console.print(f"[dim]— \\[\\[batch_table]] '{table_name}' removed from table-tool.toml (no member file left)[/]")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

@app.command(name="import")
def import_cmd(
    ctx: typer.Context,
    paths: list[Path] = typer.Argument(..., help="External file to import (more than one only with --new-batch)"),
    as_name: Optional[str] = typer.Option(
        None, "--as", help="Table name (default: filename without extension)"
    ),
    batch: Optional[str] = typer.Option(
        None, "--batch", help="Add as a member to this existing [[batch_table]]"
    ),
    new_batch: Optional[str] = typer.Option(
        None, "--new-batch", help="Create a new [[batch_table]] with this name from the given files"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite the source if a table with this name already exists"
    ),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Copies an external file into the project as a new table (or
    updates the source of an already tracked one, with --overwrite) —
    the location is always the project root, decided by the tool: no
    more creating folders or moving files by hand (see
    src/payload/docs/USAGE.md, section 'Table management'). --batch/
    --new-batch for batch tables, see src/payload/docs/BATCH.md."""

    def _run():
        require_project_root(root)
        if batch and new_batch:
            console.print("[red]✗[/] '--batch' and '--new-batch' are incompatible")
            raise typer.Exit(code=2)

        registry = load_plugins(project_root=root)
        sources, batch_tables, _ = discover_for_history(root)

        if new_batch:
            files = {}
            for p in paths:
                if not p.is_file():
                    raise SourceNotFoundError(p)
                files[p.name] = p.read_bytes()
            bt = import_new_batch_table(root, registry, files, new_batch, sources, batch_tables)
            console.print(f"[green]✓[/] \\[\\[batch_table]] '{new_batch}' created with {len(bt.source_paths)} files")
            for p in bt.source_paths:
                console.print(f"    → {p}")
            return

        if len(paths) != 1:
            console.print("[red]✗[/] one file at a time — use --new-batch to import more than one together")
            raise typer.Exit(code=2)
        external = paths[0]
        if not external.is_file():
            raise SourceNotFoundError(external)
        data = external.read_bytes()

        if batch:
            bt = next((b for b in batch_tables if b.name == batch), None)
            if bt is None:
                console.print(f"[red]✗[/] no \\[\\[batch_table]] '{batch}'")
                raise typer.Exit(code=4)
            filename = f"{as_name}{external.suffix}" if as_name else external.name
            target = import_batch_member(root, registry, data, filename, bt)
            console.print(f"[green]✓[/] {target} added to '{batch}'")
            return

        name = as_name or external.stem
        target_filename = f"{name}{external.suffix}"
        result = import_single_table(root, registry, data, target_filename, sources, batch_tables, overwrite=overwrite)
        verb = "imported" if result.created else "updated"
        console.print(f"[green]✓[/] {result.path} {verb}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# config show / report / export
# --------------------------------------------------------------------------

@config_app.command("show")
def config_show(
    ctx: typer.Context,
    table: Optional[str] = typer.Argument(None, help="Table name (to include its sidecar, if any)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Shows the resolved config and where each value comes from
    (default / global / sidecar) — useful when it's not obvious which
    of the 3 levels wins for a specific table."""

    def _run():
        require_project_root(root)
        source_path = None
        if table:
            sources, batch_tables, _ = discover_for_history(root)
            ref = resolve_table_ref(sources, batch_tables, table)
            if ref is None:
                console.print(f"[red]✗[/] table '{table}' not found")
                raise typer.Exit(code=4)
            # a batch table has no source_path to resolve a sidecar
            # from (its overrides live inline in [[batch_table]], not
            # in a <name>.config.toml file) — show only the global
            # config in that case, no sidecar to layer.
            if not ref.is_batch:
                source_path = ref.source_paths[0]

        config, provenance = resolve_config_with_provenance(root, source_path=source_path)

        t = Table(title=f"Resolved config{f' for {table}' if table else ''}")
        t.add_column("Field")
        t.add_column("Value")
        t.add_column("Origin", style="dim")

        for section_name, section_obj in (("defaults", config.defaults), ("toolchain", config.toolchain)):
            for f in dc_fields(section_obj):
                key = f"{section_name}.{f.name}"
                value = getattr(section_obj, f.name)
                origin = provenance.get(key, "default")
                style = "green" if origin == "default" else "yellow" if "sidecar" in origin else "cyan"
                t.add_row(key, str(value), f"[{style}]{origin}[/]")

        # 'plugin' is a free-form dict (not a dataclass), so it has no
        # implicit 'default' — only show keys that were actually set
        # by some level.
        for key, origin in sorted(provenance.items()):
            if key.startswith("plugin."):
                value = config.plugin
                for part in key.split(".")[1:]:
                    value = value.get(part, {}) if isinstance(value, dict) else value
                style = "yellow" if "sidecar" in origin else "cyan"
                t.add_row(key, str(value), f"[{style}]{origin}[/]")

        console.print(t)

    run_command(_run, ctx.obj["verbosity"])


@pipeline_app.command("show")
def pipeline_show(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Shows the resolved pipeline for a table (implicit 2-stage from
    --from/--to, or explicit from [pipeline] in config) — useful when
    the pipeline is long and it's not obvious at a glance what it will
    do. Also shows which stages have a valid cache checkpoint."""

    def _run():
        from payload.core.cache import compute_pipeline_cache_key, compute_pipeline_cache_key_multi
        from payload.core.pipeline import (
            final_output_paths,
            resolve_pipeline_spec,
            validate_pipeline_against_registry,
        )
        from payload.core.pipeline_spec import ExecStage, ReaderStage, WriterStage

        require_project_root(root)
        sources, batch_tables, base_config = discover_for_history(root)
        ref = resolve_table_ref(sources, batch_tables, table)
        if ref is None:
            console.print(f"[red]✗[/] table '{table}' not found")
            raise typer.Exit(code=4)

        registry = load_plugins(project_root=root)
        config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])

        spec = resolve_pipeline_spec(ref.source_paths[0], registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)

        cache = BuildCache(Path(config.defaults.cache_dir))
        config_dict = config.model_dump()
        if ref.is_batch:
            named_sources = sorted((p.name, p.read_bytes()) for p in ref.source_paths)
        else:
            source_bytes = ref.source_paths[0].read_bytes()

        t = Table(title=f"Pipeline for {table}")
        t.add_column("#")
        t.add_column("Type")
        t.add_column("Detail")
        t.add_column("Stage cache", style="dim")

        terminal_start = spec.terminal_writer_start()

        for i, stage in enumerate(spec.stages):
            if isinstance(stage, ReaderStage):
                detail = f"reader: {stage.name}"
            elif isinstance(stage, WriterStage):
                detail = f"writer: {stage.name}"
            else:
                detail = f"exec: {stage.command}"
                if stage.on_error == "warn":
                    detail += "  [dim](on_error=warn)[/]"

            checkpoint_note = ""
            if isinstance(stage, (WriterStage, ExecStage)):
                if ref.is_batch:
                    checkpoint_key = compute_pipeline_cache_key_multi(
                        named_sources, spec.signature_prefix(i), config_dict
                    )
                else:
                    checkpoint_key = compute_pipeline_cache_key(
                        source_bytes, spec.signature_prefix(i), config_dict
                    )
                stage_table_key = f"{table}::stage{i}"
                in_terminal_group = i >= terminal_start
                if in_terminal_group:
                    checkpoint_note = "[dim](final, see table cache)[/]"
                elif cache.is_fresh(stage_table_key, checkpoint_key):
                    checkpoint_note = "[green]valid[/]"
                else:
                    checkpoint_note = "[dim]none[/]"

            t.add_row(str(i), stage.kind, detail, checkpoint_note)

        console.print(t)
        out_paths = final_output_paths(spec, table, Path(config.defaults.output_dir), registry)
        destinations = ", ".join(str(p) for p in out_paths)
        console.print(f"Final output: [bold]{destinations}[/]")

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def report(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Project overview: one row per table with sizes, byte_order,
    golden status, and last history snapshot."""

    def _run():
        require_project_root(root)
        sources, batch_tables, base_config = discover_for_history(root)
        history = HistoryStore(root)
        tables = all_table_refs(sources, batch_tables)

        t = Table(title=f"Project report ({len(tables)} tables)")
        t.add_column("Table")
        t.add_column("Source")
        t.add_column("Output")
        t.add_column("Byte order")
        t.add_column("Golden")
        t.add_column("Last snapshot")

        for ref in tables:
            name = ref.name
            display = f"{name} [dim](batch, {len(ref.source_paths)} files)[/]" if ref.is_batch else name
            table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
            out_dir = Path(table_config.defaults.output_dir)

            src_size = f"{sum(p.stat().st_size for p in ref.source_paths)} B"

            output_files = list(out_dir.glob(f"{name}.*")) if out_dir.exists() else []
            if output_files:
                out_size = f"{output_files[0].stat().st_size} B"
            else:
                out_size = "[dim]never built[/]"
            golden_result = check_golden(history, name, ref.source_paths, output_files)
            golden_str = {
                "match": "[green]match[/]",
                "mismatch": "[red]mismatch[/]",
                "stale": "[yellow]stale[/]",
                "missing": "[dim]none[/]",
            }[golden_result.status]

            last = history.last_snapshot(name)
            snap_str = f"#{last.id} ({last.timestamp[:10]})" if last else "[dim]never saved[/]"

            t.add_row(display, src_size, out_size, table_config.defaults.byte_order, golden_str, snap_str)

        console.print(t)

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def export(
    ctx: typer.Context,
    output: Path = typer.Argument(..., help="Path of the .zip file to create"),
    include_history: bool = typer.Option(False, "--include-history", help="Also include .payload_history/"),
    root: Path = typer.Argument(Path(".")),
):
    """Creates a portable .zip archive with sources, config, and
    sidecars of every table in the project — useful for sharing a
    sub-project or backing it up outside of git."""

    def _run():
        from payload.export import export_project

        require_project_root(root)
        sources, batch_tables, config = discover_for_history(root)
        tables = all_table_refs(sources, batch_tables)
        # table-tool.toml (already included by export_project) carries
        # the [[batch_table]] declarations with it — the MEMBER files
        # still need to be listed explicitly here, otherwise the
        # exported project wouldn't be rebuildable (the batch's sources
        # would be missing).
        all_paths = [p for ref in tables for p in ref.source_paths]
        export_project(root, all_paths, output, include_history=include_history)
        console.print(f"[green]✓[/] {len(tables)} tables archived in {output}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# golden
# --------------------------------------------------------------------------

def _resolve_ref_or_exit(sources: list[Path], batch_tables: list, table_name: str) -> TableRef:
    ref = resolve_table_ref(sources, batch_tables, table_name)
    if ref is None:
        console.print(f"[red]✗[/] source for '{table_name}' not found")
        raise typer.Exit(code=4)
    return ref


def _current_output_paths(root: Path, base_config, ref: TableRef) -> list[Path]:
    table_config = effective_config(base_config, ref.batch) if ref.is_batch else load_config(root, source_path=ref.source_paths[0])
    out_dir = Path(table_config.defaults.output_dir)
    return list(out_dir.glob(f"{ref.name}.*")) if out_dir.exists() else []


@golden_app.command("set")
def golden_set_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot: Optional[int] = typer.Option(None, "--snapshot", help="Snapshot ID (default: the latest)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Sets which already-saved snapshot is the golden reference for a
    table — doesn't need a freshly built output, just an existing
    snapshot ('pld log <table>' to see which ones exist)."""

    def _run():
        require_project_root(root)
        history = HistoryStore(root)
        golden_id = set_golden(history, table_name, snapshot)
        console.print(f"[gold1]★[/] golden for '{table_name}' set to snapshot #{golden_id}")

    run_command(_run, ctx.obj["verbosity"])


@golden_app.command("clear")
def golden_clear_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Removes the golden reference of a table (the snapshots stay,
    only the golden pointer is removed)."""

    def _run():
        require_project_root(root)
        history = HistoryStore(root)
        if clear_golden(history, table_name):
            console.print(f"[green]✓[/] golden for '{table_name}' removed")
        else:
            console.print(f"No golden set for '{table_name}'.")

    run_command(_run, ctx.obj["verbosity"])


_GOLDEN_STATUS_STYLE = {
    "match": "[green]✓ match[/]",
    "mismatch": "[red]✗ mismatch[/]",
    "stale": "[yellow]⚠ stale (source changed after golden)[/]",
    "missing": "[dim]— no golden set[/]",
}


@golden_app.command("check")
def golden_check_cmd(
    ctx: typer.Context,
    table_name: Optional[str] = typer.Argument(None, help="Table name (default: all)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Checks the golden status of a table, or of all if omitted
    (useful in CI: exits with a non-zero code if something doesn't
    match or is stale)."""

    def _run():
        require_project_root(root)
        sources, batch_tables, config = discover_for_history(root)
        history = HistoryStore(root)

        if table_name is not None:
            ref = _resolve_ref_or_exit(sources, batch_tables, table_name)
            output_paths = _current_output_paths(root, config, ref)
            result = check_golden(history, table_name, ref.source_paths, output_paths)
            if result.status == "match":
                console.print(f"[green]✓[/] {table_name}: match")
            elif result.status == "missing":
                console.print(f"[yellow]![/] {table_name}: golden not set")
            elif result.status == "stale":
                raise GoldenStaleError(table_name)
            else:
                raise GoldenMismatchError(table_name)
            return

        any_bad = False
        for ref in all_table_refs(sources, batch_tables):
            output_paths = _current_output_paths(root, config, ref)
            result = check_golden(history, ref.name, ref.source_paths, output_paths)
            console.print(f"{ref.name}: {_GOLDEN_STATUS_STYLE[result.status]}")
            if result.status in ("mismatch", "stale"):
                any_bad = True
        if any_bad:
            raise typer.Exit(code=3)

    run_command(_run, ctx.obj["verbosity"])


@golden_app.command("diff")
def golden_diff_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Byte-for-byte differences between the current output and the golden snapshot of a table."""

    def _run():
        require_project_root(root)
        sources, batch_tables, config = discover_for_history(root)
        ref = _resolve_ref_or_exit(sources, batch_tables, table_name)
        history = HistoryStore(root)
        output_paths = _current_output_paths(root, config, ref)

        diffs = golden_diff(history, table_name, output_paths)
        if not diffs:
            console.print(f"No difference for '{table_name}'.")
            return
        for filename, chunks in diffs.items():
            console.print(f"[bold]Diff for {filename}[/]")
            for c in chunks:
                console.print(
                    f"0x{c['offset']:04X}  current: {c['current']}  |  golden: {c['golden']}",
                    style="red",
                )

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# plugins list / plugin new
# --------------------------------------------------------------------------

@app.command(name="plugins")
def plugins_list(ctx: typer.Context):
    """Lists the registered readers/writers/doctor-checks."""

    def _run():
        registry = load_plugins(strict=False)
        table = Table(title="Registered plugins")
        table.add_column("Kind", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Extensions")
        table.add_column("API")

        for kind, items in (
            ("reader", registry.readers),
            ("writer", registry.writers),
            ("doctor_check", registry.doctor_checks),
        ):
            for name, plugin in items.items():
                ext = getattr(plugin, "extensions", None) or [getattr(plugin, "extension", "")]
                table.add_row(kind, name, ", ".join(e for e in ext if e), getattr(plugin, "api_version", "?"))

        console.print(table)
        console.print("[dim]💡 'pld plugin info <name>' shows the documentation of a specific plugin[/]")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("info")
def plugin_info(ctx: typer.Context, name: str = typer.Argument(..., help="Name of the registered reader/writer/doctor-check")):
    """Shows a plugin's documentation: its class docstring is the
    source — a well-written plugin has a docstring explaining the
    format it handles, not just an attribute list."""

    def _run():
        registry = load_plugins(strict=False)
        plugin = registry.readers.get(name) or registry.writers.get(name) or registry.doctor_checks.get(name)
        if plugin is None:
            console.print(f"[red]✗[/] plugin '{name}' not found")
            console.print("    → use 'pld plugins' to see the available ones", style="dim")
            raise typer.Exit(code=4)

        kind = "reader" if name in registry.readers else "writer" if name in registry.writers else "doctor_check"
        doc = (type(plugin).__doc__ or "").strip()

        lines = [f"[bold]{name}[/] ({kind}, API v{getattr(plugin, 'api_version', '?')})"]
        if hasattr(plugin, "extensions"):
            lines.append(f"extensions: {', '.join(plugin.extensions)}")
        if hasattr(plugin, "extension"):
            lines.append(f"output extension: {plugin.extension}")
        default_writer = getattr(plugin, "default_writer", None)
        if default_writer:
            lines.append(f"suggested writer: {default_writer}")
        compatible = getattr(plugin, "compatible_readers", None)
        if compatible:
            lines.append(f"only compatible with: {', '.join(compatible)}")
        lines.append("")
        lines.append(doc if doc else "[dim](no docstring — the plugin author didn't document anything)[/]")

        console.print(Panel("\n".join(lines), title=f"Documentation: {name}", border_style="cyan"))

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("install-deps")
def plugin_install_deps(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Path of the local .py plugin (with REQUIRES declared)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
):
    """Installs with pip the dependencies declared by a local plugin
    (module-level REQUIRES = [...]). Has nothing to do with a plugin
    installed via pip (that one already manages its own dependencies,
    through its pyproject.toml)."""

    def _run():
        from payload.core.local_plugins import missing_requirements, read_requires_static

        requires = read_requires_static(file)
        if not requires:
            console.print(f"[yellow]![/] '{file.name}' declares no REQUIRES, nothing to install")
            return

        missing = missing_requirements(requires)
        if not missing:
            console.print(f"[green]✓[/] every dependency of '{file.name}' is already installed")
            return

        console.print(f"Missing dependencies for {file.name}: {', '.join(missing)}")
        if not yes and not typer.confirm("Install them now with pip in the current environment?"):
            console.print("Cancelled.")
            return

        cmd = [sys.executable, "-m", "pip", "install", *missing]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            console.print(f"[red]✗[/] installation failed (exit {result.returncode})")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/] installed: {', '.join(missing)}")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("validate")
def plugin_validate(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the registered reader/writer to validate"),
    sample: Optional[Path] = typer.Option(
        None, "--sample", help="Valid sample file for the reader (required for readers)"
    ),
):
    """Verifies that an already-installed plugin honors the
    Reader/Writer contract, at runtime. Doesn't need pytest: it's the
    same conformance suite usable from 'payload.testing' in your own
    tests."""

    def _run():
        from payload.core.ir import TableIR
        from payload.testing import (
            check_reader_behavior,
            check_reader_structure,
            check_writer_behavior,
            check_writer_structure,
        )

        registry = load_plugins(strict=False)
        issues = []

        if name in registry.readers:
            reader = registry.readers[name]
            issues += check_reader_structure(reader)
            if sample is None:
                console.print(
                    "[yellow]![/] no --sample provided: skipping behavioral checks "
                    "(structure only verified)"
                )
            else:
                issues += check_reader_behavior(reader, sample)
        elif name in registry.writers:
            writer = registry.writers[name]
            issues += check_writer_structure(writer)
            sample_ir = TableIR(
                name="conformance_sample", data=b"\x00\x01\x02",
                source_path=Path("conformance_sample"), source_format="testing",
            )
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                issues += check_writer_behavior(writer, sample_ir, Path(tmp))
        else:
            console.print(f"[red]✗[/] plugin '{name}' not found among the registered ones")
            raise typer.Exit(code=4)

        if not issues:
            console.print(f"[green]✓[/] {name}: conforms to the contract")
            return

        console.print(f"[red]✗[/] {name}: {len(issues)} contract violations")
        for issue in issues:
            console.print(f"    [{issue.check}] {issue.detail}", style="red")
        raise typer.Exit(code=1)

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("new-local")
def plugin_new_local(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Plugin name (slug), e.g. simple_reader"),
    kind: str = typer.Option(..., "--kind", help="reader | writer | doctor-check"),
    dest: Path = typer.Option(Path("local_plugins"), "--dest", help="Destination folder"),
):
    """Quick scaffold of a LOCAL plugin: a single .py file inside
    local_plugins/, no pip install. For a distributable plugin (a real
    pip package), use 'pld plugin new' instead."""

    def _run():
        try:
            out_path = scaffold_local_plugin(name, kind, dest)
        except ValueError:
            console.print(f"[red]✗[/] unknown kind: '{kind}' (reader|writer|doctor-check)")
            raise typer.Exit(code=2)
        except FileExistsError:
            console.print(f"[red]✗[/] '{dest / (name.replace('-', '_') + '.py')}' already exists")
            raise typer.Exit(code=2)

        console.print(f"[green]✓[/] created {out_path}")
        console.print("    → 'pld plugins' to check it gets discovered once you've finished it", style="dim")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("new")
def plugin_new(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Package name, e.g. payload-reader-csv"),
    kind: str = typer.Option(..., "--kind", help="reader | writer | doctor-check"),
    dest: Path = typer.Option(Path("."), "--dest", help="Destination folder"),
):
    """Generates the scaffold of a new installable plugin."""

    def _run():
        out_dir = scaffold_plugin(name, kind, dest)
        console.print(f"[green]✓[/] Plugin scaffolded in {out_dir}")
        console.print(f"    → cd {out_dir} && pip install -e .", style="dim")

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def clean(
    ctx: typer.Context,
    target: str = typer.Option(
        "cache", "--target",
        help="What to clean: cache | build | golden | all",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
):
    """Empties the cache, build output, or golden references. Useful
    during development when you want to start fresh with no leftover
    traces. 'golden' is no longer a folder: it removes the golden
    pointers of every table, the snapshots stay intact."""

    def _run():
        require_project_root(Path.cwd())
        config = load_config(Path.cwd())
        if target not in ("cache", "build", "golden", "all"):
            console.print(f"[red]✗[/] unknown target: '{target}' (cache|build|golden|all)")
            raise typer.Exit(code=2)

        dirs = []
        if target in ("cache", "all"):
            dirs.append(Path(config.defaults.cache_dir))
        if target in ("build", "all"):
            dirs.append(Path(config.defaults.output_dir))
        existing = [d for d in dirs if d.exists()]

        history = HistoryStore(Path.cwd())
        golden_map = history.all_golden() if target in ("golden", "all") else {}

        if not existing and not golden_map:
            console.print("Nothing to clean.")
            return

        if not yes:
            parts = [str(d) for d in existing]
            if golden_map:
                parts.append(f"golden references ({len(golden_map)} tables)")
            console.print(f"The following will be deleted: {', '.join(parts)}")
            if not typer.confirm("Confirm?"):
                console.print("Cancelled.")
                return

        for d in existing:
            shutil.rmtree(d)
            console.print(f"[green]✓[/] removed {d}")

        if golden_map:
            for name in golden_map:
                history.clear_golden(name)
            console.print(f"[green]✓[/] removed golden references for {len(golden_map)} tables")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------

@app.command()
def serve(
    ctx: typer.Context,
    root: Path = typer.Argument(Path("."), help="Project folder to serve"),
    host: str = typer.Option("127.0.0.1", "--host", help="Address to listen on"),
    port: int = typer.Option(8420, "--port", help="Port to listen on"),
):
    """Starts a local web server with a graphical interface for every
    payload feature — useful for those who prefer not to use the
    terminal. Requires the optional 'serve' extra (pip install
    'payload[serve]')."""

    def _run():
        require_project_root(root.resolve())
        try:
            import uvicorn

            from payload.web.app import create_app
        except ImportError:
            console.print("[red]✗[/] web dependencies not installed")
            console.print(r"    → run: pip install 'payload\[serve]'", style="dim")
            raise typer.Exit(code=2)

        if host not in ("127.0.0.1", "localhost", "::1"):
            err_console.print(Panel(
                f"[bold]WARNING[/]: server exposed on [bold]{host}[/], not just localhost.\n"
                "Anyone reaching this address on the network can trigger builds\n"
                "(including 'exec' stages, which run arbitrary system commands)\n"
                "and modify project files. Only use this on trusted networks.",
                title="[red]⚠ Server exposed beyond localhost[/]", border_style="red",
            ))

        web_app = create_app(root.resolve())
        console.print(f"[green]✓[/] payload serve on [bold]http://{host}:{port}[/]  (root: {root.resolve()})")
        console.print("[dim]Ctrl+C to stop[/]")
        uvicorn.run(web_app, host=host, port=port, log_level="warning")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

@app.command()
def doctor(ctx: typer.Context):
    """Checks toolchain, plugins, config, and directories before a batch build."""

    def _run():
        require_project_root(Path.cwd())
        registry = load_plugins(strict=False)
        config = load_config(Path.cwd())
        config_dict = config.model_dump()
        config_dict["_project_root"] = str(Path.cwd())

        with console.status("[cyan]Running system checks...[/]", spinner="dots"):
            results = run_doctor(config_dict, registry)

        for r in results:
            style = STATUS_STYLE[r.status]
            icon = STATUS_ICON[r.status]
            console.print(f"[{style}]{icon}[/] {r.name}: {r.message}")
            if r.hint and r.status != CheckStatus.OK:
                console.print(f"    → {r.hint}", style="dim")

        n_fail = sum(1 for r in results if r.status == CheckStatus.FAIL)
        n_warn = sum(1 for r in results if r.status == CheckStatus.WARN)
        n_ok = len(results) - n_fail - n_warn
        summary_style = "red" if n_fail else ("yellow" if n_warn else "green")
        console.print(
            Panel(
                f"[green]{n_ok} ok[/]   [yellow]{n_warn} warnings[/]   [red]{n_fail} failed[/]",
                title="Doctor summary",
                border_style=summary_style,
            )
        )

        if n_fail:
            raise typer.Exit(code=2)

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

@app.command()
def init(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None,
        help="Name of the new folder to create for the project. "
             "If omitted, confirmation is asked to use the current folder.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
    wizard: bool = typer.Option(
        False, "--wizard", "-w",
        help="Guided mode: asks for project name, what to include, whether to init git",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="No questions: use defaults everywhere (works with --wizard, for scripts/CI)",
    ),
):
    """Creates the minimal scaffold of a project: config, directories,
    local_plugins/, sample table.

    With a name, creates a new dedicated folder (recommended: avoids
    accidentally ending up with the scaffold scattered in the wrong
    folder). Without a name, asks for explicit confirmation before
    writing to the current folder. With --wizard, walks step by step
    through the choices instead of using all the defaults."""

    def _run():
        resolved_name = name
        include_local_plugins = True
        include_example = True
        chosen_writer = None
        chosen_byte_order = "little"
        do_git_init = False

        if wizard:
            print_banner(console)
            console.print("[bold]Initialization wizard[/]\n")

            if resolved_name is None and not yes:
                typed = typer.prompt(
                    "Project name (ENTER to use the current folder)", default=""
                )
                resolved_name = typed or None

            if not yes:
                include_local_plugins = typer.confirm(
                    "Create 'local_plugins/' for external plugins without pip install?", default=True
                )
                include_example = typer.confirm(
                    "Include a sample table?", default=True
                )
                writer_choice = typer.prompt(
                    "Default writer (bin/hex/obj, ENTER for no preference)", default=""
                )
                chosen_writer = writer_choice or None
                chosen_byte_order = typer.prompt(
                    "Default byte order (little/big)", default="little"
                )
                do_git_init = typer.confirm(
                    "Initialize a git repository in this folder?", default=False
                )
            console.print()

        if resolved_name is not None:
            target_dir = Path.cwd() / resolved_name
            if is_nonempty_existing_dir(target_dir) and not force:
                console.print(f"[red]✗[/] '{resolved_name}' already exists and isn't empty.")
                console.print("    → use --force to write anyway, or choose another name", style="dim")
                raise typer.Exit(code=2)
            just_created_dir = not target_dir.exists()
        else:
            target_dir = Path.cwd()
            just_created_dir = False
            if is_nonempty_existing_dir(target_dir) and not force and not yes:
                n_items = len(list(target_dir.iterdir()))
                console.print(
                    f"[yellow]![/] the current folder ({target_dir}) already contains {n_items} items."
                )
                if not typer.confirm("Initialize here anyway?"):
                    console.print(
                        "Cancelled. Tip: [bold]pld init <project-name>[/] "
                        "creates a new dedicated folder, safer."
                    )
                    raise typer.Exit(code=0)

        init_kwargs = dict(
            force=force,
            include_local_plugins=include_local_plugins,
            include_example=include_example,
        )
        if wizard:
            # only in wizard mode do we pass explicit writer/byte_order
            # (even None if the user expresses no preference) — without
            # the wizard, init_project uses its historical default
            # (writer 'bin')
            init_kwargs["writer"] = chosen_writer
            init_kwargs["byte_order"] = chosen_byte_order

        created = init_project(target_dir, **init_kwargs)

        if do_git_init:
            if shutil.which("git") is None:
                console.print("[yellow]![/] git not found in PATH, skipping repository initialization")
            else:
                git_result = subprocess.run(
                    ["git", "init"], cwd=target_dir, capture_output=True, text=True
                )
                if git_result.returncode == 0:
                    console.print(f"[green]✓[/] git repository initialized in {target_dir}")
                else:
                    console.print(f"[yellow]![/] 'git init' failed: {git_result.stderr.strip()}")

        if not wizard:
            print_banner(console)
        for p in created:
            console.print(f"[green]✓[/] {p}")

        next_steps = f"cd {resolved_name}\n" if just_created_dir else ""
        next_steps += "pld doctor"
        if include_example:
            next_steps += "\npld build example_table.raw"
        console.print(Panel(next_steps, title="Next steps", border_style="green"))
        console.print(f"[dim]💡 {random_tip()}[/]")

    run_command(_run, ctx.obj["verbosity"])


if __name__ == "__main__":  # pragma: no cover - only run as 'python -m payload.cli', a subprocess separate from the test process (see test_module_entry_point_runs_as_script)
    app()
