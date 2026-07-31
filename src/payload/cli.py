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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from payload.core.cache import BuildCache
from payload.core.config import load_config, resolve_config_with_provenance
from payload.core.discovery import discover_table_sources, find_duplicate_stems
from payload.core.doctor import run_doctor
from payload.core.errors import (
    BatchBuildError,
    DuplicateTableNameError,
    GoldenMismatchError,
    InvalidCliOptionError,
    NothingToCommitError,
    PayloadError,
)
from payload.core.golden import check_golden, update_golden
from payload.core.history import HistoryStore
from payload.core.logging_setup import setup_logging
from payload.core.pipeline import build
from payload.core.plugin_base import CheckStatus
from payload.core.registry import load_plugins
from payload._version import __version__
from payload.init_cmd import init_project, is_nonempty_existing_dir
from payload.plugin_scaffold import scaffold_plugin
from payload.ui.banner import print_banner, random_tip
from payload.ui.flavor import random_loading_phrase
from payload.watch import watch as watch_loop

app = typer.Typer(name="pld", help="payload — gestione tabelle per sistemi embedded")
golden_app = typer.Typer(help="Gestione golden file")
plugin_app = typer.Typer(help="Gestione/scaffold plugin")
config_app = typer.Typer(help="Ispezione della configurazione risolta")
app.add_typer(golden_app, name="golden")
app.add_typer(plugin_app, name="plugin")
app.add_typer(config_app, name="config")

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
):
    """Compila una singola tabella."""

    def _run():
        registry = load_plugins()
        config = load_config(Path.cwd(), source_path=source)
        cache = BuildCache(Path(config.defaults.cache_dir))
        cli_opts = _parse_opts(opt)

        with console.status(f"[cyan]{random_loading_phrase()}[/]", spinner="dots"):
            out_path, was_built = build(
                source, registry, config, out, cache=cache,
                reader_name=from_, writer_name=to, force=force, dry_run=dry_run,
                cli_opts=cli_opts,
            )
            cache.save()

            if check_golden_flag and not dry_run:
                result = check_golden(out_path, Path(config.defaults.golden_dir))
                if result.status == "mismatch":
                    raise GoldenMismatchError(out_path)

        status = "costruito" if was_built else "da cache"
        console.print(f"[green]✓[/] {source.name} → {out_path} ({status})")

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

        results = {"built": 0, "cached": 0, "golden_mismatch": 0, "errors": 0}
        failures = []
        results_lock = threading.Lock()

        def _build_one(src: Path):
            """Eseguita in un thread del pool. Ritorna sempre, non solleva:
            gli errori sono catturati qui per non far crashare l'executor
            e per accumulare tutti i fallimenti, non solo il primo."""
            try:
                per_table_config = load_config(root, source_path=src)
                out_path, was_built = build(
                    src, registry, per_table_config, out, cache=cache,
                    writer_name=to, force=force, dry_run=dry_run,
                    cli_opts=cli_opts,
                )
                mismatch = False
                if check_golden_flag and not dry_run:
                    gresult = check_golden(out_path, Path(per_table_config.defaults.golden_dir))
                    mismatch = gresult.status == "mismatch"
                return ("ok", src, was_built, mismatch, None)
            except PayloadError as e:
                return ("error", src, False, False, e)

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(random_loading_phrase(), total=len(sources))

            # jobs=1 -> stesso comportamento sequenziale di prima, nessun
            # overhead di thread pool. jobs>1 -> parallelizza, dato che le
            # tabelle sono indipendenti tra loro (nessuna reference incrociata).
            with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
                futures = {executor.submit(_build_one, src): src for src in sources}
                for future in as_completed(futures):
                    status, src, was_built, mismatch, error = future.result()
                    with results_lock:
                        progress.update(task, description=f"[cyan]{src.name}[/]")
                        if status == "ok":
                            results["built" if was_built else "cached"] += 1
                            if mismatch:
                                results["golden_mismatch"] += 1
                        else:
                            results["errors"] += 1
                            failures.append(error)
                        progress.advance(task)

        cache.save()

        summary_style = "red" if results["errors"] else ("yellow" if results["golden_mismatch"] else "green")
        console.print(
            Panel(
                f"[green]{results['built']}[/] costruite   "
                f"[cyan]{results['cached']}[/] da cache   "
                f"[yellow]{results['golden_mismatch']}[/] golden mismatch   "
                f"[red]{results['errors']}[/] errori",
                title=f"{len(sources)} tabelle processate",
                border_style=summary_style,
            )
        )
        if random.random() < 0.3:
            console.print(f"[dim]💡 {random_tip()}[/]")

        if failures:
            raise BatchBuildError(failures)

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
):
    """Rebuild automatico ad ogni salvataggio (Ctrl+C per uscire)."""

    def _run():
        registry = load_plugins(project_root=root)
        config = load_config(root)
        cache = BuildCache(Path(config.defaults.cache_dir))
        known_ext = {ext for r in registry.readers.values() for ext in r.extensions}

        def on_change(src: Path):
            per_table_config = load_config(root, source_path=src)
            out_path, was_built = build(
                src, registry, per_table_config, out, cache=cache, writer_name=to,
            )
            cache.save()
            status = "ricostruito" if was_built else "invariato (cache)"
            console.print(f"[green]✓[/] {src.name} → {out_path} ({status})")

        watch_loop(root if root.is_dir() else root.parent, known_ext, out, on_change)

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

def _discover_for_history(root: Path):
    """Helper condiviso: stesso identico insieme di tabelle che vedrebbe
    build-all, così status/commit non disaccordano mai su cosa esiste."""
    registry = load_plugins(project_root=root)
    config = load_config(root)
    known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
    sources = discover_table_sources(root, known_ext, Path(config.defaults.output_dir))

    duplicates = find_duplicate_stems(sources)
    if duplicates:
        raise DuplicateTableNameError(duplicates)

    return sources, config


@app.command()
def status(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Mostra quali tabelle sono cambiate rispetto all'ultimo snapshot."""

    def _run():
        sources, _ = _discover_for_history(root)
        history = HistoryStore(root)

        table = Table(title="Stato tabelle")
        table.add_column("Tabella")
        table.add_column("Stato")

        any_change = False
        for src in sources:
            name = src.stem
            last = history.last_snapshot(name)
            if last is None:
                table.add_row(src.name, "[yellow]mai salvata[/]")
                any_change = True
            elif history.is_dirty(name, src):
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
    root: Path = typer.Argument(Path(".")),
):
    """Salva uno snapshot di sorgente + output generato per ogni tabella
    modificata (o solo per quelle indicate con --only)."""

    def _run():
        sources, config = _discover_for_history(root)
        history = HistoryStore(root)
        output_dir = Path(config.defaults.output_dir)

        if only:
            sources = [s for s in sources if s.stem in only]

        dirty = [s for s in sources if history.is_dirty(s.stem, s)]
        if not dirty:
            raise NothingToCommitError()

        for src in dirty:
            output_paths = list(output_dir.glob(f"{src.stem}.*"))
            snap = history.commit(src.stem, src, output_paths, message)
            n_out = len(snap.output_blobs)
            console.print(f"[green]✓[/] {src.stem} → snapshot #{snap.id} ({n_out} output allegati)")

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
            console.print(f"[bold]{name}[/]")
            for s in reversed(snapshots):
                n_out = len(s.output_blobs)
                console.print(f"  #{s.id}  {s.timestamp}  {s.message}  [dim]({n_out} output)[/]")

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

        sources, _ = _discover_for_history(root)
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

        sources, config = _discover_for_history(root)
        src = next((s for s in sources if s.stem == table_name), None)
        if src is None:
            console.print(f"[red]✗[/] sorgente per '{table_name}' non trovato")
            raise typer.Exit(code=4)

        if not yes:
            console.print(
                f"Verranno sovrascritti {src.name} e i relativi output "
                f"con lo stato dello snapshot #{snapshot_id}."
            )
            if not typer.confirm("Confermi?"):
                console.print("Annullato.")
                return

        written = history.restore(table_name, snapshot_id, src, Path(config.defaults.output_dir))
        for w in written:
            console.print(f"[green]✓[/] ripristinato {w}")

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
            sources, _ = _discover_for_history(root)
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


@app.command()
def report(ctx: typer.Context, root: Path = typer.Argument(Path("."))):
    """Vista d'insieme del progetto: una riga per tabella con dimensioni,
    byte_order, stato golden e ultimo snapshot history."""

    def _run():
        sources, _ = _discover_for_history(root)
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
                golden_result = check_golden(output_files[0], Path(table_config.defaults.golden_dir))
                golden_str = {
                    "match": "[green]match[/]",
                    "mismatch": "[red]mismatch[/]",
                    "missing": "[yellow]nessuno[/]",
                }[golden_result.status]
            else:
                out_size = "[dim]mai buildata[/]"
                golden_str = "[dim]—[/]"

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

        sources, config = _discover_for_history(root)
        export_project(root, sources, output, include_history=include_history)
        console.print(f"[green]✓[/] {len(sources)} tabelle archiviate in {output}")

    run_command(_run, ctx.obj["verbosity"])


# --------------------------------------------------------------------------
# golden
# --------------------------------------------------------------------------

@golden_app.command("update")
def golden_update(
    ctx: typer.Context,
    target: Path = typer.Argument(..., help="File di output o cartella build"),
):
    def _run():
        config = load_config(Path.cwd())
        gdir = Path(config.defaults.golden_dir)
        targets = [target] if target.is_file() else list(target.rglob("*"))
        for t in targets:
            if t.is_file():
                gpath = update_golden(t, gdir)
                console.print(f"[green]✓[/] golden aggiornato: {gpath}")

    run_command(_run, ctx.obj["verbosity"])


@golden_app.command("check")
def golden_check(ctx: typer.Context, target: Path = typer.Argument(...)):
    def _run():
        config = load_config(Path.cwd())
        gdir = Path(config.defaults.golden_dir)
        result = check_golden(target, gdir)
        if result.status == "match":
            console.print(f"[green]✓[/] {target.name}: match")
        elif result.status == "missing":
            console.print(f"[yellow]![/] {target.name}: golden mancante")
        else:
            raise GoldenMismatchError(target)

    run_command(_run, ctx.obj["verbosity"])


@golden_app.command("diff")
def golden_diff(ctx: typer.Context, target: Path = typer.Argument(...)):
    def _run():
        config = load_config(Path.cwd())
        gdir = Path(config.defaults.golden_dir)
        result = check_golden(target, gdir)
        if result.status != "mismatch":
            console.print(f"Nessuna differenza ({result.status})")
            return
        console.print(f"[bold]Diff per {target.name}[/]")
        max_len = max(len(result.current), len(result.expected))
        for i in range(0, max_len, 8):
            cur_chunk = result.current[i:i + 8]
            exp_chunk = result.expected[i:i + 8]
            if cur_chunk != exp_chunk:
                console.print(
                    f"0x{i:04X}  attuale: {cur_chunk.hex(' ')}  |  golden: {exp_chunk.hex(' ')}",
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
    """Svuota cache, output di build, o golden file. Utile durante lo
    sviluppo quando si vuole ripartire da zero senza tracce residue."""

    def _run():
        config = load_config(Path.cwd())
        dirs_by_target = {
            "cache": [Path(config.defaults.cache_dir)],
            "build": [Path(config.defaults.output_dir)],
            "golden": [Path(config.defaults.golden_dir)],
        }
        dirs_by_target["all"] = [d for dirs in dirs_by_target.values() for d in dirs]

        if target not in dirs_by_target:
            console.print(f"[red]✗[/] target sconosciuto: '{target}' (cache|build|golden|all)")
            raise typer.Exit(code=2)

        dirs = dirs_by_target[target]
        existing = [d for d in dirs if d.exists()]
        if not existing:
            console.print("Niente da pulire.")
            return

        if not yes:
            console.print(f"Verranno cancellate: {', '.join(str(d) for d in existing)}")
            if not typer.confirm("Confermi?"):
                console.print("Annullato.")
                return

        for d in existing:
            shutil.rmtree(d)
            console.print(f"[green]✓[/] rimossa {d}")

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


if __name__ == "__main__":
    app()
