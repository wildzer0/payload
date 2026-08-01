"""
CLI di payload (pld). Un unico punto di cattura delle eccezioni
(run_command) decide exit code e formato di stampa: i singoli comandi
restano puliti e sollevano solo le eccezioni della gerarchia in
payload.core.errors.
"""
from __future__ import annotations

import logging
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
from payload.core.cache import BuildCache
from payload.core.config import load_config, resolve_config_with_provenance
from payload.core.discovery import discover_for_history, discover_table_sources, find_duplicate_stems
from payload.core.doctor import run_doctor
from payload.core.errors import (
    BatchBuildError,
    DuplicateTableNameError,
    GoldenMismatchError,
    GoldenStaleError,
    InvalidCliOptionError,
    NothingToCommitError,
    PayloadError,
)
from payload.core.golden import check_golden, clear_golden, golden_diff, set_golden
from payload.core.history import HistoryStore
from payload.core.logging_setup import setup_logging
from payload.core.pipeline import build, describe_table_build
from payload.core.plugin_base import CheckStatus
from payload.core.registry import load_plugins
from payload._version import __version__
from payload.init_cmd import init_project, is_nonempty_existing_dir
from payload.plugin_scaffold import scaffold_local_plugin, scaffold_plugin
from payload.ui.banner import print_banner, random_tip
from payload.ui.flavor import random_loading_phrase
from payload.watch import watch as watch_loop

app = typer.Typer(name="pld", help="payload — gestione tabelle per sistemi embedded")
golden_app = typer.Typer(help="Gestione golden file")
plugin_app = typer.Typer(help="Gestione/scaffold plugin")
config_app = typer.Typer(help="Ispezione della configurazione risolta")
pipeline_app = typer.Typer(help="Ispezione della pipeline")
app.add_typer(golden_app, name="golden")
app.add_typer(plugin_app, name="plugin")
app.add_typer(config_app, name="config")
app.add_typer(pipeline_app, name="pipeline")

# Console Windows con codepage legacy (cp1252/'charmap', non UTF-8) non
# sanno rappresentare emoji come 💡 usate nei tip — senza questo, un
# UnicodeEncodeError durante la scrittura fa crashare l'intero comando
# per un dettaglio puramente estetico. errors="replace" sostituisce il
# carattere non rappresentabile con un placeholder invece di sollevare,
# non ha alcun effetto quando lo stream supporta già UTF-8 (il caso
# comune su Linux/macOS e Windows Terminal moderno). Deve avvenire
# PRIMA di creare le istanze Console sotto, altrimenti rich potrebbe
# aver già letto l'encoding originale dello stream.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:  # pragma: no cover - puramente cosmetico, non deve MAI bloccare l'avvio
            # 'pytest' (e altri contesti che sostituiscono sys.stdout/stderr
            # con wrapper custom, es. cattura output) possono esporre un
            # attributo 'reconfigure' che si comporta diversamente da un
            # vero io.TextIOWrapper e sollevare eccezioni impreviste — un
            # except ristretto a ValueError/OSError non le copriva tutte,
            # rompendo l'IMPORT dell'intero modulo (quindi ogni test).
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
    verbose: int = typer.Option(0, "-v", count=True, help="Aumenta verbosità (-v, -vv)"),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Mostra la versione ed esce",
    ),
):
    ctx.ensure_object(dict)
    ctx.obj["verbosity"] = verbose
    setup_logging(verbose)


def run_command(fn, verbosity: int):
    """Punto unico di cattura: decide exit code e formato di stampa per
    ogni PayloadError. Bug interni (eccezioni non previste) restano
    distinti e mostrano sempre traceback pieno.

    NOTA: typer.Exit va ri-sollevato SENZA passare per il ramo
    'Exception' generico. È l'eccezione con cui typer implementa
    un'uscita pulita (usata da diversi comandi con
    'raise typer.Exit(code=...)' dopo aver già stampato un messaggio
    chiaro) — se non la si intercetta esplicitamente prima del generico
    'except Exception', un'uscita controllata (es. 'doctor' con check
    falliti) viene scambiata per un crash del tool, con tanto di
    traceback fuorviante mostrato all'utente."""
    logger = logging.getLogger("payload.cli")
    try:
        return fn()
    except PayloadError as e:
        logger.log(e.log_level, e.message, extra=e.context, exc_info=verbosity >= 2)
        err_console.print(f"[red]✗[/] {e.message}")
        if e.hint:
            err_console.print(f"    → {e.hint}", style="dim")
        if verbosity >= 2:
            stderr_text = e.context.get("stderr")
            stdout_text = e.context.get("stdout")
            if stderr_text:
                err_console.print("\n[bold]--- stderr del comando ---[/]")
                err_console.print(stderr_text)
            if stdout_text:
                err_console.print("\n[bold]--- stdout del comando ---[/]")
                err_console.print(stdout_text)
        raise typer.Exit(code=e.exit_code)
    except typer.Exit:
        raise
    except Exception as e:  # bug del tool, non errore "atteso"
        err_console.print(f"[red]✗ Errore interno inatteso:[/] {e}")
        err_console.print_exception()
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# build / build-all
# --------------------------------------------------------------------------

def _parse_opts(raw_opts: Optional[list[str]]) -> dict:
    """Converte una lista di 'chiave=valore' (da --opt ripetuto) in un
    dict. Override una tantum per questa invocazione, letti dai plugin
    con config.get("cli_opts", {}).get("chiave") — non persistono in
    nessun file."""
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
    source: Path = typer.Argument(..., help="File sorgente della tabella"),
    from_: Optional[str] = typer.Option(None, "--from", help="Reader esplicito"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer da usare"),
    out: Path = typer.Option(Path("build"), "--out", help="Directory di output"),
    force: bool = typer.Option(False, "--force", help="Bypassa la cache"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra cosa farebbe senza eseguire"),
    check_golden_flag: bool = typer.Option(
        False, "--check-golden", help="Fallisce se l'output non combacia col golden"
    ),
    opt: Optional[list[str]] = typer.Option(
        None, "--opt", help="Override chiave=valore per il plugin attivo, es. --opt delimiter=; (ripetibile)"
    ),
    keep_intermediate: bool = typer.Option(
        False, "--keep-intermediate", help="Non ripulisce tmp/ dopo la build (debug pipeline multi-stage)"
    ),
):
    """Compila una singola tabella."""

    def _run():
        registry = load_plugins()
        config = load_config(Path.cwd(), source_path=source)
        cache = BuildCache(Path(config.defaults.cache_dir))
        cli_opts = _parse_opts(opt)

        with console.status(f"[cyan]{random_loading_phrase()}[/]", spinner="dots"):
            out_paths, was_built = build(
                source, registry, config, out, cache=cache,
                reader_name=from_, writer_name=to, force=force, dry_run=dry_run,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate,
            )
            cache.save()

            if check_golden_flag and not dry_run:
                history = HistoryStore(Path.cwd())
                result = check_golden(history, source.stem, source, out_paths)
                if result.status == "mismatch":
                    raise GoldenMismatchError(source.stem)
                if result.status == "stale":
                    raise GoldenStaleError(source.stem)

        status = "costruito" if was_built else "da cache"
        destinations = ", ".join(str(p) for p in out_paths)
        console.print(f"[green]✓[/] {source.name} → {destinations} ({status})")

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="build-all")
def build_all_cmd(
    ctx: typer.Context,
    root: Path = typer.Argument(Path("."), help="Cartella radice da scansionare"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer da usare"),
    out: Path = typer.Option(Path("build"), "--out", help="Directory di output"),
    jobs: int = typer.Option(1, "--jobs", help="Grado di parallelismo"),
    filter_glob: Optional[str] = typer.Option(None, "--filter", help="Glob per filtrare i sorgenti"),
    force: bool = typer.Option(False, "--force"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    check_golden_flag: bool = typer.Option(False, "--check-golden"),
    opt: Optional[list[str]] = typer.Option(
        None, "--opt", help="Override chiave=valore per il plugin attivo, applicato a tutte le tabelle (ripetibile)"
    ),
    keep_intermediate: bool = typer.Option(
        False, "--keep-intermediate", help="Non ripulisce tmp/ dopo ogni build (debug pipeline multi-stage)"
    ),
):
    """Batch build ricorsivo su tutte le tabelle trovate sotto root."""

    def _run():
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

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(random_loading_phrase(), total=len(sources))

            def _on_result(src: Path, status: str) -> None:
                progress.update(task, description=f"[cyan]{src.name}[/]")
                progress.advance(task)

            # jobs=1 -> stesso comportamento sequenziale di prima, nessun
            # overhead di thread pool. jobs>1 -> parallelizza, dato che le
            # tabelle sono indipendenti tra loro (nessuna reference incrociata).
            summary = run_batch_build(
                sources, root, registry, cache, out, jobs=jobs, writer_name=to,
                force=force, dry_run=dry_run, check_golden_flag=check_golden_flag,
                cli_opts=cli_opts, keep_intermediate=keep_intermediate,
                on_table_result=_on_result,
            )

        summary_style = "red" if summary.errors else ("yellow" if summary.golden_mismatch else "green")
        console.print(
            Panel(
                f"[green]{summary.built}[/] costruite   "
                f"[cyan]{summary.cached}[/] da cache   "
                f"[yellow]{summary.golden_mismatch}[/] golden mismatch   "
                f"[red]{summary.errors}[/] errori",
                title=f"{len(sources)} tabelle processate",
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
    root: Path = typer.Argument(Path("."), help="File o cartella da osservare"),
    to: Optional[str] = typer.Option(None, "--to", help="Writer da usare"),
    out: Path = typer.Option(Path("build"), "--out", help="Directory di output"),
    jobs: int = typer.Option(1, "--jobs", help="Grado di parallelismo per la build iniziale"),
    filter_glob: Optional[str] = typer.Option(
        None, "--filter", help="Glob per filtrare i sorgenti nella build iniziale (non nel watch live)"
    ),
):
    """Build iniziale di tutte le tabelle sotto 'root', poi rebuild
    automatico ad ogni salvataggio (Ctrl+C per uscire)."""

    def _run():
        project_root = Path.cwd()
        registry = load_plugins(project_root=project_root)
        config = load_config(project_root)
        cache = BuildCache(Path(config.defaults.cache_dir))
        known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
        watch_root = root if root.is_dir() else root.parent

        # La build iniziale non deve mai impedire l'avvio del watch —
        # stessa filosofia di payload/watch.py, che non muore mai per un
        # errore di build durante l'osservazione live.
        try:
            sources = discover_table_sources(root, known_ext, Path(config.defaults.output_dir), filter_glob)
            duplicates = find_duplicate_stems(sources)
            if duplicates:
                raise DuplicateTableNameError(duplicates)
            summary = run_batch_build(sources, root, registry, cache, out, jobs=jobs, writer_name=to)
            if summary.failures:
                console.print(
                    f"[yellow]![/] build iniziale: {len(summary.failures)}/{len(sources)} "
                    "tabelle fallite — procedo comunque con il watch"
                )
            else:
                console.print(
                    f"[green]✓[/] build iniziale: {summary.built} costruite, "
                    f"{summary.cached} da cache ({len(sources)} tabelle)"
                )
        except PayloadError as e:
            console.print(f"[yellow]![/] build iniziale fallita ({e.message}) — procedo comunque con il watch")

        def on_change(src: Path):
            per_table_config = load_config(project_root, source_path=src)
            out_paths, was_built = build(
                src, registry, per_table_config, out, cache=cache, writer_name=to,
            )
            cache.save()
            status = "ricostruito" if was_built else "invariato (cache)"
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
    """Visualizza il contenuto raw (bytes + commenti) di una tabella."""

    def _run():
        registry = load_plugins()
        reader = registry.find_reader(source, from_)
        ir = reader.parse(source, {})

        table = Table(title=f"{ir.name} ({len(ir.data)} bytes)")
        table.add_column("Offset", style="dim")
        table.add_column("Bytes")
        table.add_column("Commento", style="italic")

        comments_by_offset = dict(ir.comments)
        for i in range(0, len(ir.data), 8):
            chunk = ir.data[i:i + 8]
            hex_str = " ".join(f"{b:02X}" for b in chunk)
            comment = comments_by_offset.get(i, "")
            table.add_row(f"0x{i:04X}", hex_str, comment)

        console.print(table)

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# status / commit / log / diff / restore  (checkpoint leggero per tabella)
# --------------------------------------------------------------------------

@app.command()
def status(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Mostra quali tabelle sono cambiate rispetto all'ultimo snapshot."""

    def _run():
        sources, config = discover_for_history(root)
        history = HistoryStore(root)
        output_dir = Path(config.defaults.output_dir)

        table = Table(title="Stato tabelle")
        table.add_column("Tabella")
        table.add_column("Stato")

        any_change = False
        for src in sources:
            name = src.stem
            output_paths = list(output_dir.glob(f"{name}.*")) if output_dir.exists() else []
            last = history.last_snapshot(name)
            if last is None:
                table.add_row(src.name, "[yellow]mai salvata[/]")
                any_change = True
            elif history.is_dirty(name, src, output_paths):
                table.add_row(src.name, "[red]modificata[/]")
                any_change = True
            else:
                table.add_row(src.name, "[green]invariata[/]")

        console.print(table)
        if sources and not any_change:
            console.print("[dim]Nessuna modifica da salvare.[/]")
        elif not sources:
            console.print("[dim]Nessuna tabella trovata sotto questa cartella.[/]")

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def commit(
    ctx: typer.Context,
    message: str = typer.Option(..., "-m", "--message", help="Messaggio dello snapshot"),
    only: Optional[list[str]] = typer.Option(
        None, "--only", help="Limita ai nomi tabella indicati (ripetibile, es. --only t1 --only t2)"
    ),
    golden: bool = typer.Option(
        False, "--golden", help="Imposta anche il nuovo snapshot come golden per ogni tabella committata"
    ),
    root: Path = typer.Argument(Path(".")),
):
    """Salva uno snapshot di sorgente + output generato per ogni tabella
    modificata (o solo per quelle indicate con --only)."""

    def _run():
        sources, config = discover_for_history(root)
        history = HistoryStore(root)
        registry = load_plugins(project_root=root)
        output_dir = Path(config.defaults.output_dir)

        if only:
            sources = [s for s in sources if s.stem in only]

        dirty = []
        for s in sources:
            output_paths = list(output_dir.glob(f"{s.stem}.*"))
            if history.is_dirty(s.stem, s, output_paths):
                table_config = load_config(root, source_path=s)
                build_info = describe_table_build(s, registry, table_config, output_paths, output_dir)
                dirty.append((s, output_paths, build_info))
        if not dirty:
            raise NothingToCommitError()

        for src, output_paths, build_info in dirty:
            snap = history.commit(src.stem, src, output_paths, message, **build_info)
            n_out = len(snap.output_blobs)
            suffix = ""
            if golden:
                history.set_golden(src.stem, snap.id)
                suffix = " [gold1]★ golden[/]"
            console.print(f"[green]✓[/] {src.stem} → snapshot #{snap.id} ({n_out} output allegati){suffix}")
            if snap.missing_outputs:
                console.print(
                    f"    [yellow]![/] pipeline incompleta: manca {', '.join(snap.missing_outputs)} "
                    "(un writer del gruppo non ha prodotto output — verifica prima di fidarti di questo snapshot)"
                )

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="log")
def log_cmd(
    ctx: typer.Context,
    table_name: Optional[str] = typer.Argument(None, help="Se omesso, mostra tutte le tabelle tracciate"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Storico degli snapshot, come 'git log'."""

    def _run():
        history = HistoryStore(root)
        names = [table_name] if table_name else history.all_tracked_tables()

        if not names:
            console.print("Nessuna tabella tracciata ancora. Usa [bold]pld commit -m \"...\"[/] per iniziare.")
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
                marker = "  [cyan]● attuale[/]" if s.id == head_id else ""
                warn = f"  [yellow]![/] pipeline incompleta, manca {', '.join(s.missing_outputs)}" if s.missing_outputs else ""
                console.print(
                    f"  #{s.id}  {s.timestamp}  {s.message}  [dim]({outputs})[/]{pipeline_str}{marker}{warn}"
                )

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="diff")
def diff_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot: Optional[int] = typer.Option(None, "--snapshot", help="ID snapshot da confrontare (default: ultimo)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Confronta il sorgente attuale con uno snapshot salvato."""

    def _run():
        history = HistoryStore(root)
        snap_id = snapshot
        if snap_id is None:
            last = history.last_snapshot(table_name)
            if last is None:
                console.print(f"[yellow]![/] nessuno snapshot per '{table_name}'")
                raise typer.Exit(code=5)
            snap_id = last.id

        snap = history.get_snapshot(table_name, snap_id)

        sources, _ = discover_for_history(root)
        src = next((s for s in sources if s.stem == table_name), None)
        if src is None:
            console.print(f"[red]✗[/] sorgente per '{table_name}' non trovato")
            raise typer.Exit(code=4)

        current = src.read_bytes()
        expected = history.read_blob(snap.source_blob)

        if current == expected:
            console.print(f"Nessuna differenza rispetto allo snapshot #{snap_id}")
            return

        console.print(f"[bold]Diff {src.name} vs snapshot #{snap_id}[/]")
        max_len = max(len(current), len(expected))
        for i in range(0, max_len, 8):
            c_chunk = current[i:i + 8]
            e_chunk = expected[i:i + 8]
            if c_chunk != e_chunk:
                console.print(
                    f"0x{i:04X}  attuale: {c_chunk.hex(' ')}  |  snapshot: {e_chunk.hex(' ')}",
                    style="red",
                )

    run_command(_run, ctx.obj["verbosity"])


@app.command(name="restore")
def restore_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot_id: int = typer.Argument(..., help="ID dello snapshot a cui tornare (vedi 'pld log')"),
    root: Path = typer.Option(Path("."), "--root"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma"),
):
    """Riporta sorgente E output generato allo stato di uno snapshot precedente."""

    def _run():
        history = HistoryStore(root)
        history.get_snapshot(table_name, snapshot_id)  # valida che esista, solleva se manca

        sources, config = discover_for_history(root)
        src = next((s for s in sources if s.stem == table_name), None)
        if src is None:
            console.print(f"[red]✗[/] sorgente per '{table_name}' non trovato")
            raise typer.Exit(code=4)

        if not yes:
            console.print(
                f"Verranno sovrascritti {src.name} e i relativi output "
                f"con lo stato dello snapshot #{snapshot_id}. "
                "Nessun nuovo snapshot viene creato: solo l'attuale si sposta indietro, "
                "la cronologia resta intatta."
            )
            if not typer.confirm("Confermi?"):
                console.print("Annullato.")
                return

        result = history.restore(table_name, snapshot_id, src, Path(config.defaults.output_dir))
        for w in result.written:
            console.print(f"[green]✓[/] ripristinato {w}")
        for r in result.removed:
            console.print(f"[yellow]—[/] rimosso (non fa parte dello snapshot #{snapshot_id}): {r}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# config show / report / export
# --------------------------------------------------------------------------

@config_app.command("show")
def config_show(
    ctx: typer.Context,
    table: Optional[str] = typer.Argument(None, help="Nome tabella (per includere l'eventuale sidecar)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Mostra il config risolto e da dove viene ogni valore
    (default / globale / sidecar) — utile quando non è ovvio quale
    livello dei 3 sta vincendo per una tabella specifica."""

    def _run():
        source_path = None
        if table:
            sources, _ = discover_for_history(root)
            src = next((s for s in sources if s.stem == table), None)
            if src is None:
                console.print(f"[red]✗[/] tabella '{table}' non trovata")
                raise typer.Exit(code=4)
            source_path = src

        config, provenance = resolve_config_with_provenance(root, source_path=source_path)

        t = Table(title=f"Config risolta{f' per {table}' if table else ''}")
        t.add_column("Campo")
        t.add_column("Valore")
        t.add_column("Origine", style="dim")

        for section_name, section_obj in (("defaults", config.defaults), ("toolchain", config.toolchain)):
            for f in dc_fields(section_obj):
                key = f"{section_name}.{f.name}"
                value = getattr(section_obj, f.name)
                origin = provenance.get(key, "default")
                style = "green" if origin == "default" else "yellow" if "sidecar" in origin else "cyan"
                t.add_row(key, str(value), f"[{style}]{origin}[/]")

        # 'plugin' è un dict libero (non un dataclass), quindi non ha
        # 'default' impliciti — mostriamo solo le chiavi che sono
        # state effettivamente impostate da qualche livello.
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
    table: str = typer.Argument(..., help="Nome tabella"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Mostra la pipeline risolta per una tabella (implicita a 2 stage
    da --from/--to, o esplicita da [pipeline] in config) — utile
    quando la pipeline è lunga e non è ovvio a colpo d'occhio cosa
    farà. Mostra anche quali stage hanno un checkpoint di cache valido."""

    def _run():
        from payload.core.cache import compute_pipeline_cache_key
        from payload.core.pipeline import (
            final_output_paths,
            resolve_pipeline_spec,
            validate_pipeline_against_registry,
        )
        from payload.core.pipeline_spec import ExecStage, ReaderStage, WriterStage

        sources, _ = discover_for_history(root)
        src = next((s for s in sources if s.stem == table), None)
        if src is None:
            console.print(f"[red]✗[/] tabella '{table}' non trovata")
            raise typer.Exit(code=4)

        registry = load_plugins(project_root=root)
        config = load_config(root, source_path=src)

        spec = resolve_pipeline_spec(src, registry, config, None, None)
        validate_pipeline_against_registry(spec, registry)

        cache = BuildCache(Path(config.defaults.cache_dir))
        source_bytes = src.read_bytes()
        config_dict = config.model_dump()

        t = Table(title=f"Pipeline per {table}")
        t.add_column("#")
        t.add_column("Tipo")
        t.add_column("Dettaglio")
        t.add_column("Cache stage", style="dim")

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
                checkpoint_key = compute_pipeline_cache_key(
                    source_bytes, spec.signature_prefix(i), config_dict
                )
                stage_table_key = f"{src}::stage{i}"
                in_terminal_group = i >= terminal_start
                if in_terminal_group:
                    checkpoint_note = "[dim](finale, vedi cache tabella)[/]"
                elif cache.is_fresh(stage_table_key, checkpoint_key):
                    checkpoint_note = "[green]valido[/]"
                else:
                    checkpoint_note = "[dim]nessuno[/]"

            t.add_row(str(i), stage.kind, detail, checkpoint_note)

        console.print(t)
        out_paths = final_output_paths(spec, src, Path(config.defaults.output_dir), registry)
        destinations = ", ".join(str(p) for p in out_paths)
        console.print(f"Output finale: [bold]{destinations}[/]")

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def report(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Vista d'insieme del progetto: una riga per tabella con dimensioni,
    byte_order, stato golden e ultimo snapshot history."""

    def _run():
        sources, _ = discover_for_history(root)
        history = HistoryStore(root)

        t = Table(title=f"Report progetto ({len(sources)} tabelle)")
        t.add_column("Tabella")
        t.add_column("Sorgente")
        t.add_column("Output")
        t.add_column("Byte order")
        t.add_column("Golden")
        t.add_column("Ultimo snapshot")

        for src in sources:
            name = src.stem
            table_config = load_config(root, source_path=src)
            out_dir = Path(table_config.defaults.output_dir)

            src_size = f"{src.stat().st_size} B"

            output_files = list(out_dir.glob(f"{name}.*")) if out_dir.exists() else []
            if output_files:
                out_size = f"{output_files[0].stat().st_size} B"
            else:
                out_size = "[dim]mai buildata[/]"
            golden_result = check_golden(history, name, src, output_files)
            golden_str = {
                "match": "[green]match[/]",
                "mismatch": "[red]mismatch[/]",
                "stale": "[yellow]stale[/]",
                "missing": "[dim]nessuno[/]",
            }[golden_result.status]

            last = history.last_snapshot(name)
            snap_str = f"#{last.id} ({last.timestamp[:10]})" if last else "[dim]mai salvata[/]"

            t.add_row(name, src_size, out_size, table_config.defaults.byte_order, golden_str, snap_str)

        console.print(t)

    run_command(_run, ctx.obj["verbosity"])


@app.command()
def export(
    ctx: typer.Context,
    output: Path = typer.Argument(..., help="Percorso del file .zip da creare"),
    include_history: bool = typer.Option(False, "--include-history", help="Include anche .payload_history/"),
    root: Path = typer.Argument(Path(".")),
):
    """Crea un archivio .zip portabile con sorgenti, config e sidecar
    di tutte le tabelle del progetto — utile per condividere un
    sotto-progetto o farne backup fuori da git."""

    def _run():
        from payload.export import export_project

        sources, config = discover_for_history(root)
        export_project(root, sources, output, include_history=include_history)
        console.print(f"[green]✓[/] {len(sources)} tabelle archiviate in {output}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# golden
# --------------------------------------------------------------------------

def _table_source(root: Path, sources: list[Path], table_name: str) -> Path:
    src = next((s for s in sources if s.stem == table_name), None)
    if src is None:
        console.print(f"[red]✗[/] sorgente per '{table_name}' non trovato")
        raise typer.Exit(code=4)
    return src


def _current_output_paths(root: Path, table_name: str, src: Path) -> list[Path]:
    table_config = load_config(root, source_path=src)
    out_dir = Path(table_config.defaults.output_dir)
    return list(out_dir.glob(f"{table_name}.*")) if out_dir.exists() else []


@golden_app.command("set")
def golden_set_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    snapshot: Optional[int] = typer.Option(None, "--snapshot", help="ID dello snapshot (default: l'ultimo)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Imposta quale snapshot già salvato è il riferimento golden per
    una tabella — non serve un output appena buildato, solo uno
    snapshot esistente ('pld log <tabella>' per vedere quali ci sono)."""

    def _run():
        history = HistoryStore(root)
        golden_id = set_golden(history, table_name, snapshot)
        console.print(f"[gold1]★[/] golden per '{table_name}' impostato allo snapshot #{golden_id}")

    run_command(_run, ctx.obj["verbosity"])


@golden_app.command("clear")
def golden_clear_cmd(
    ctx: typer.Context,
    table_name: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Rimuove il riferimento golden di una tabella (gli snapshot
    restano, solo il puntatore golden viene tolto)."""

    def _run():
        history = HistoryStore(root)
        if clear_golden(history, table_name):
            console.print(f"[green]✓[/] golden per '{table_name}' rimosso")
        else:
            console.print(f"Nessun golden impostato per '{table_name}'.")

    run_command(_run, ctx.obj["verbosity"])


_GOLDEN_STATUS_STYLE = {
    "match": "[green]✓ match[/]",
    "mismatch": "[red]✗ mismatch[/]",
    "stale": "[yellow]⚠ stale (sorgente cambiato dopo il golden)[/]",
    "missing": "[dim]— nessun golden impostato[/]",
}


@golden_app.command("check")
def golden_check_cmd(
    ctx: typer.Context,
    table_name: Optional[str] = typer.Argument(None, help="Nome tabella (default: tutte)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Verifica lo stato golden di una tabella, o di tutte se omessa
    (utile in CI: esce con codice diverso da zero se qualcosa non
    combacia o è stale)."""

    def _run():
        sources, _ = discover_for_history(root)
        history = HistoryStore(root)

        if table_name is not None:
            src = _table_source(root, sources, table_name)
            output_paths = _current_output_paths(root, table_name, src)
            result = check_golden(history, table_name, src, output_paths)
            if result.status == "match":
                console.print(f"[green]✓[/] {table_name}: match")
            elif result.status == "missing":
                console.print(f"[yellow]![/] {table_name}: golden non impostato")
            elif result.status == "stale":
                raise GoldenStaleError(table_name)
            else:
                raise GoldenMismatchError(table_name)
            return

        any_bad = False
        for src in sources:
            name = src.stem
            output_paths = _current_output_paths(root, name, src)
            result = check_golden(history, name, src, output_paths)
            console.print(f"{name}: {_GOLDEN_STATUS_STYLE[result.status]}")
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
    """Differenze byte per byte tra l'output attuale e lo snapshot golden di una tabella."""

    def _run():
        sources, _ = discover_for_history(root)
        src = _table_source(root, sources, table_name)
        history = HistoryStore(root)
        output_paths = _current_output_paths(root, table_name, src)

        diffs = golden_diff(history, table_name, output_paths)
        if not diffs:
            console.print(f"Nessuna differenza per '{table_name}'.")
            return
        for filename, chunks in diffs.items():
            console.print(f"[bold]Diff per {filename}[/]")
            for c in chunks:
                console.print(
                    f"0x{c['offset']:04X}  attuale: {c['current']}  |  golden: {c['golden']}",
                    style="red",
                )

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# plugins list / plugin new
# --------------------------------------------------------------------------

@app.command(name="plugins")
def plugins_list(ctx: typer.Context):
    """Elenca i reader/writer/doctor-check registrati."""

    def _run():
        registry = load_plugins(strict=False)
        table = Table(title="Plugin registrati")
        table.add_column("Tipo", style="cyan")
        table.add_column("Nome", style="bold")
        table.add_column("Estensioni")
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
        console.print("[dim]💡 'pld plugin info <nome>' mostra la documentazione di un plugin specifico[/]")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("info")
def plugin_info(ctx: typer.Context, name: str = typer.Argument(..., help="Nome del reader/writer/doctor-check")):
    """Mostra la documentazione di un plugin: la docstring della sua
    classe è la fonte — un plugin ben scritto ha una docstring che
    spiega il formato che gestisce, non solo un elenco di attributi."""

    def _run():
        registry = load_plugins(strict=False)
        plugin = registry.readers.get(name) or registry.writers.get(name) or registry.doctor_checks.get(name)
        if plugin is None:
            console.print(f"[red]✗[/] plugin '{name}' non trovato")
            console.print("    → usa 'pld plugins' per vedere quelli disponibili", style="dim")
            raise typer.Exit(code=4)

        kind = "reader" if name in registry.readers else "writer" if name in registry.writers else "doctor_check"
        doc = (type(plugin).__doc__ or "").strip()

        lines = [f"[bold]{name}[/] ({kind}, API v{getattr(plugin, 'api_version', '?')})"]
        if hasattr(plugin, "extensions"):
            lines.append(f"estensioni: {', '.join(plugin.extensions)}")
        if hasattr(plugin, "extension"):
            lines.append(f"estensione output: {plugin.extension}")
        default_writer = getattr(plugin, "default_writer", None)
        if default_writer:
            lines.append(f"writer suggerito: {default_writer}")
        compatible = getattr(plugin, "compatible_readers", None)
        if compatible:
            lines.append(f"compatibile solo con: {', '.join(compatible)}")
        lines.append("")
        lines.append(doc if doc else "[dim](nessuna docstring — l'autore del plugin non ha documentato nulla)[/]")

        console.print(Panel("\n".join(lines), title=f"Documentazione: {name}", border_style="cyan"))

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("install-deps")
def plugin_install_deps(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Percorso del plugin locale .py (con REQUIRES dichiarato)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma"),
):
    """Installa con pip le dipendenze dichiarate da un plugin locale
    (REQUIRES = [...] a livello di modulo). Non ha nulla a che fare con
    un plugin installato via pip (quello gestisce già le proprie
    dipendenze da solo, tramite il suo pyproject.toml)."""

    def _run():
        from payload.core.local_plugins import missing_requirements, read_requires_static

        requires = read_requires_static(file)
        if not requires:
            console.print(f"[yellow]![/] '{file.name}' non dichiara REQUIRES, niente da installare")
            return

        missing = missing_requirements(requires)
        if not missing:
            console.print(f"[green]✓[/] tutte le dipendenze di '{file.name}' sono già installate")
            return

        console.print(f"Dipendenze mancanti per {file.name}: {', '.join(missing)}")
        if not yes and not typer.confirm("Installarle ora con pip nell'ambiente corrente?"):
            console.print("Annullato.")
            return

        cmd = [sys.executable, "-m", "pip", "install", *missing]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            console.print(f"[red]✗[/] installazione fallita (exit {result.returncode})")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/] installate: {', '.join(missing)}")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("validate")
def plugin_validate(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Nome del reader/writer registrato da validare"),
    sample: Optional[Path] = typer.Option(
        None, "--sample", help="File di esempio valido per il reader (obbligatorio per i reader)"
    ),
):
    """Verifica che un plugin già installato rispetti il contratto
    Reader/Writer, a runtime. Non richiede pytest: è la stessa suite
    di conformità usabile anche da 'payload.testing' nei propri test."""

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
                    "[yellow]![/] nessun --sample fornito: salto i check comportamentali "
                    "(solo struttura verificata)"
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
            console.print(f"[red]✗[/] plugin '{name}' non trovato tra i registrati")
            raise typer.Exit(code=4)

        if not issues:
            console.print(f"[green]✓[/] {name}: conforme al contratto")
            return

        console.print(f"[red]✗[/] {name}: {len(issues)} violazioni del contratto")
        for issue in issues:
            console.print(f"    [{issue.check}] {issue.detail}", style="red")
        raise typer.Exit(code=1)

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("new-local")
def plugin_new_local(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Nome del plugin (slug), es. simple_reader"),
    kind: str = typer.Option(..., "--kind", help="reader | writer | doctor-check"),
    dest: Path = typer.Option(Path("local_plugins"), "--dest", help="Cartella di destinazione"),
):
    """Scaffold rapido di un plugin LOCALE: un singolo file .py dentro
    local_plugins/, senza pip install. Per un plugin distribuibile
    (pacchetto pip vero), usa invece 'pld plugin new'."""

    def _run():
        try:
            out_path = scaffold_local_plugin(name, kind, dest)
        except ValueError:
            console.print(f"[red]✗[/] kind sconosciuto: '{kind}' (reader|writer|doctor-check)")
            raise typer.Exit(code=2)
        except FileExistsError:
            console.print(f"[red]✗[/] '{dest / (name.replace('-', '_') + '.py')}' esiste già")
            raise typer.Exit(code=2)

        console.print(f"[green]✓[/] creato {out_path}")
        console.print("    → 'pld plugins' per verificare che venga scoperto dopo averlo completato", style="dim")

    run_command(_run, ctx.obj["verbosity"])


@plugin_app.command("new")
def plugin_new(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Nome pacchetto, es. payload-reader-csv"),
    kind: str = typer.Option(..., "--kind", help="reader | writer | doctor-check"),
    dest: Path = typer.Option(Path("."), "--dest", help="Cartella di destinazione"),
):
    """Genera lo scaffold di un nuovo plugin installabile."""

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
        help="Cosa pulire: cache | build | golden | all",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma"),
):
    """Svuota cache, output di build, o i riferimenti golden. Utile
    durante lo sviluppo quando si vuole ripartire da zero senza tracce
    residue. 'golden' non è più una cartella: rimuove i puntatori
    golden di tutte le tabelle, gli snapshot restano intatti."""

    def _run():
        config = load_config(Path.cwd())
        if target not in ("cache", "build", "golden", "all"):
            console.print(f"[red]✗[/] target sconosciuto: '{target}' (cache|build|golden|all)")
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
            console.print("Niente da pulire.")
            return

        if not yes:
            parts = [str(d) for d in existing]
            if golden_map:
                parts.append(f"riferimenti golden ({len(golden_map)} tabelle)")
            console.print(f"Verranno cancellate: {', '.join(parts)}")
            if not typer.confirm("Confermi?"):
                console.print("Annullato.")
                return

        for d in existing:
            shutil.rmtree(d)
            console.print(f"[green]✓[/] rimossa {d}")

        if golden_map:
            for name in golden_map:
                history.clear_golden(name)
            console.print(f"[green]✓[/] rimossi riferimenti golden per {len(golden_map)} tabelle")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------

@app.command()
def serve(
    ctx: typer.Context,
    root: Path = typer.Argument(Path("."), help="Cartella progetto da servire"),
    host: str = typer.Option("127.0.0.1", "--host", help="Indirizzo su cui ascoltare"),
    port: int = typer.Option(8420, "--port", help="Porta su cui ascoltare"),
):
    """Avvia un server web locale con interfaccia grafica per tutte le
    funzionalità di payload — utile per chi preferisce non usare il
    terminale. Richiede l'extra opzionale 'serve' (pip install
    'payload[serve]')."""

    def _run():
        try:
            import uvicorn

            from payload.web.app import create_app
        except ImportError:
            console.print("[red]✗[/] dipendenze web non installate")
            console.print(r"    → esegui: pip install 'payload\[serve]'", style="dim")
            raise typer.Exit(code=2)

        if host not in ("127.0.0.1", "localhost", "::1"):
            err_console.print(Panel(
                f"[bold]ATTENZIONE[/]: server esposto su [bold]{host}[/], non solo localhost.\n"
                "Chiunque raggiunga questo indirizzo in rete può avviare build\n"
                "(inclusi stage 'exec', che eseguono comandi di sistema arbitrari)\n"
                "e modificare file del progetto. Usa solo su reti fidate.",
                title="[red]⚠ Server esposto oltre localhost[/]", border_style="red",
            ))

        web_app = create_app(root.resolve())
        console.print(f"[green]✓[/] payload serve su [bold]http://{host}:{port}[/]  (root: {root.resolve()})")
        console.print("[dim]Ctrl+C per fermare[/]")
        uvicorn.run(web_app, host=host, port=port, log_level="warning")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

@app.command()
def doctor(ctx: typer.Context):
    """Verifica toolchain, plugin, config e directory prima di un batch build."""

    def _run():
        registry = load_plugins(strict=False)
        config = load_config(Path.cwd())
        config_dict = config.model_dump()
        config_dict["_project_root"] = str(Path.cwd())

        with console.status("[cyan]Eseguo i check di sistema...[/]", spinner="dots"):
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
                f"[green]{n_ok} ok[/]   [yellow]{n_warn} warning[/]   [red]{n_fail} falliti[/]",
                title="Riepilogo doctor",
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
        help="Nome della cartella nuova da creare per il progetto. "
             "Se omesso, viene chiesta conferma per usare la cartella corrente.",
    ),
    force: bool = typer.Option(False, "--force", help="Sovrascrive file esistenti"),
    wizard: bool = typer.Option(
        False, "--wizard", "-w",
        help="Modalità guidata: chiede nome progetto, cosa includere, se inizializzare git",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Nessuna domanda: usa i default ovunque (compatibile con --wizard, per script/CI)",
    ),
):
    """Crea lo scaffold minimo di un progetto: config, directory,
    local_plugins/, tabella d'esempio.

    Con un nome, crea una cartella nuova dedicata (consigliato: evita di
    finire con lo scaffold sparso nella cartella sbagliata per errore).
    Senza nome, chiede conferma esplicita prima di scrivere nella
    cartella corrente. Con --wizard, guida passo passo attraverso le
    scelte invece di usare tutti i default."""

    def _run():
        resolved_name = name
        include_local_plugins = True
        include_example = True
        chosen_writer = None
        chosen_byte_order = "little"
        do_git_init = False

        if wizard:
            print_banner(console)
            console.print("[bold]Wizard di inizializzazione[/]\n")

            if resolved_name is None and not yes:
                typed = typer.prompt(
                    "Nome del progetto (INVIO per usare la cartella corrente)", default=""
                )
                resolved_name = typed or None

            if not yes:
                include_local_plugins = typer.confirm(
                    "Creare 'local_plugins/' per plugin esterni senza pip install?", default=True
                )
                include_example = typer.confirm(
                    "Includere una tabella di esempio?", default=True
                )
                writer_choice = typer.prompt(
                    "Writer di default (bin/hex/obj, INVIO per nessuna preferenza)", default=""
                )
                chosen_writer = writer_choice or None
                chosen_byte_order = typer.prompt(
                    "Byte order di default (little/big)", default="little"
                )
                do_git_init = typer.confirm(
                    "Inizializzare un repository git in questa cartella?", default=False
                )
            console.print()

        if resolved_name is not None:
            target_dir = Path.cwd() / resolved_name
            if is_nonempty_existing_dir(target_dir) and not force:
                console.print(f"[red]✗[/] '{resolved_name}' esiste già e non è vuota.")
                console.print("    → usa --force per scrivere comunque, o scegli un altro nome", style="dim")
                raise typer.Exit(code=2)
            just_created_dir = not target_dir.exists()
        else:
            target_dir = Path.cwd()
            just_created_dir = False
            if is_nonempty_existing_dir(target_dir) and not force and not yes:
                n_items = len(list(target_dir.iterdir()))
                console.print(
                    f"[yellow]![/] la cartella corrente ({target_dir}) contiene già {n_items} elementi."
                )
                if not typer.confirm("Vuoi comunque inizializzare qui?"):
                    console.print(
                        "Annullato. Suggerimento: [bold]pld init <nome-progetto>[/] "
                        "crea una cartella nuova dedicata, più sicuro."
                    )
                    raise typer.Exit(code=0)

        init_kwargs = dict(
            force=force,
            include_local_plugins=include_local_plugins,
            include_example=include_example,
        )
        if wizard:
            # solo in modalità wizard passiamo writer/byte_order espliciti
            # (anche None se l'utente non esprime preferenza) — senza
            # wizard, init_project usa il suo default storico (writer 'bin')
            init_kwargs["writer"] = chosen_writer
            init_kwargs["byte_order"] = chosen_byte_order

        created = init_project(target_dir, **init_kwargs)

        if do_git_init:
            if shutil.which("git") is None:
                console.print("[yellow]![/] git non trovato nel PATH, salto l'inizializzazione del repository")
            else:
                git_result = subprocess.run(
                    ["git", "init"], cwd=target_dir, capture_output=True, text=True
                )
                if git_result.returncode == 0:
                    console.print(f"[green]✓[/] repository git inizializzato in {target_dir}")
                else:
                    console.print(f"[yellow]![/] 'git init' fallito: {git_result.stderr.strip()}")

        if not wizard:
            print_banner(console)
        for p in created:
            console.print(f"[green]✓[/] {p}")

        next_steps = f"cd {resolved_name}\n" if just_created_dir else ""
        next_steps += "pld doctor"
        if include_example:
            next_steps += "\npld build example_table.raw"
        console.print(Panel(next_steps, title="Prossimi passi", border_style="green"))
        console.print(f"[dim]💡 {random_tip()}[/]")

    run_command(_run, ctx.obj["verbosity"])


if __name__ == "__main__":  # pragma: no cover - eseguito solo come 'python -m payload.cli', un sottoprocesso separato dal processo di test (vedi test_module_entry_point_runs_as_script)
    app()
