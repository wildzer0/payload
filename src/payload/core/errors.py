"""
Gerarchia delle eccezioni del tool.

Ogni eccezione porta:
- un exit_code coerente con la convenzione CLI
- un log_level per il logging automatico nel punto di cattura centrale
- un hint opzionale mostrato all'utente
- un contesto strutturato (path, nome plugin, ecc.) utile per log/debug

I plugin (reader/writer/doctor check) sollevano queste eccezioni e basta:
non le catturano, non le loggano, non decidono formato di stampa. Tutto
questo avviene in un unico punto (cli.run_command), garantendo che plugin
scritti da persone diverse producano errori dall'aspetto coerente.
"""
from __future__ import annotations

import logging
from pathlib import Path


class PayloadError(Exception):
    """Base di tutte le eccezioni del tool."""

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


# --- Exit code 1: errori di build --------------------------------------

class BuildError(PayloadError):
    exit_code = 1


class ReaderParseError(BuildError):
    """Il reader non riesce a interpretare il sorgente."""

    def __init__(self, path: Path, reason: str, **kw):
        super().__init__(f"Errore parsing '{path}': {reason}", path=str(path), **kw)


class ToolchainExecutionError(BuildError):
    """Un comando esterno (compilatore/objcopy/...) ha fallito."""

    def __init__(self, command: list[str], returncode: int, stderr: str = "", **kw):
        super().__init__(
            f"Comando '{' '.join(command)}' fallito (exit {returncode})",
            hint="Esegui con -vv per vedere l'output completo del comando",
            command=command,
            returncode=returncode,
            stderr=stderr,
            **kw,
        )


class WriterEmitError(BuildError):
    """Il writer non riesce a produrre l'output."""

    def __init__(self, writer_name: str, reason: str, **kw):
        super().__init__(f"Writer '{writer_name}' non può generare output: {reason}", **kw)


class FanOutWriteError(BuildError):
    """Un fan-out terminale (reader -> più writer, tutti alimentati
    dalla stessa IR) ha completato SOLO una parte dei writer: quelli
    fallliti hanno sollevato un errore, quelli riusciti hanno comunque
    scritto il proprio output su disco (sono indipendenti l'uno
    dall'altro). Il messaggio elenca ESPLICITAMENTE successi e
    fallimenti — altrimenti un errore generico nasconderebbe che 2
    file su 3 sono realmente stati costruiti, lasciando l'utente senza
    modo di saperlo."""

    def __init__(self, succeeded: list[Path], failures: list[tuple[str, str]], **kw):
        total = len(succeeded) + len(failures)
        ok_desc = ", ".join(p.name for p in succeeded) if succeeded else "nessuno"
        fail_desc = "; ".join(f"'{name}': {reason}" for name, reason in failures)
        super().__init__(
            f"Fan-out parziale: {len(succeeded)}/{total} writer completati ({ok_desc}) — falliti: {fail_desc}",
            hint="Gli output riusciti sono comunque su disco: verifica prima di committare stato incompleto",
            succeeded_outputs=[str(p) for p in succeeded],
            failed_writers=[{"writer": name, "reason": reason} for name, reason in failures],
            **kw,
        )


class BatchBuildError(BuildError):
    """Aggregato: una o più tabelle hanno fallito in build-all."""

    def __init__(self, failures: list[PayloadError], **kw):
        super().__init__(
            f"{len(failures)} tabelle fallite sul totale del batch",
            failures=[f.to_dict() for f in failures],
            **kw,
        )


# --- Exit code 2: config / plugin ---------------------------------------

class ConfigError(PayloadError):
    exit_code = 2


class InvalidConfigError(ConfigError):
    def __init__(self, path: Path, field: str, reason: str, **kw):
        super().__init__(
            f"Config non valida in '{path}': campo '{field}' — {reason}",
            hint="Esegui 'pld doctor' per un check completo della config",
            path=str(path),
            field=field,
            **kw,
        )


class InvalidPipelineError(ConfigError):
    """Regole di alternanza violate (reader/writer/exec) — vedi
    src/payload/docs/PIPELINE.md. Sollevato PRIMA di eseguire qualunque stage."""

    def __init__(self, stage_index: int, reason: str, **kw):
        super().__init__(
            f"Pipeline non valida allo stage #{stage_index}: {reason}",
            hint="Vedi src/payload/docs/PIPELINE.md per le regole di alternanza reader/writer/exec",
            stage_index=stage_index,
            **kw,
        )


class PluginError(ConfigError):
    pass


class PluginLoadError(PluginError):
    def __init__(self, plugin_name: str, group: str, reason: str, **kw):
        super().__init__(
            f"Plugin '{plugin_name}' ({group}) non caricabile: {reason}",
            hint="Verifica che il pacchetto sia installato correttamente",
            plugin_name=plugin_name,
            group=group,
            **kw,
        )


class MissingPluginDependenciesError(PluginError):
    def __init__(self, path: Path, missing: list[str], **kw):
        super().__init__(
            f"Plugin locale '{path.name}': dipendenze mancanti: {', '.join(missing)}",
            hint=f"Esegui 'pld plugin install-deps {path}' per installarle",
            path=str(path), missing=missing, **kw,
        )


class PluginApiVersionError(PluginError):
    """Il plugin dichiara una versione dell'API incompatibile col core."""

    def __init__(self, plugin_name: str, plugin_api_version: str, core_api_version: str, **kw):
        super().__init__(
            f"Plugin '{plugin_name}' richiede API v{plugin_api_version}, "
            f"il core espone v{core_api_version}",
            hint="Aggiorna il plugin o il core così che le versioni combacino",
            plugin_name=plugin_name,
            plugin_api_version=plugin_api_version,
            core_api_version=core_api_version,
            **kw,
        )


class AmbiguousReaderError(PluginError):
    def __init__(self, path: Path, candidates: list[str], **kw):
        super().__init__(
            f"Più reader candidati per '{path}': {', '.join(candidates)}",
            hint="Specifica esplicitamente con --from <reader>",
            path=str(path),
            candidates=candidates,
            **kw,
        )


class WriterNotSpecifiedError(ConfigError):
    """Nessun writer risolvibile: né --to, né config, né un default
    suggerito dal reader."""

    def __init__(self, path: Path, reader_name: str, **kw):
        super().__init__(
            f"Nessun writer specificato per '{path}' (reader '{reader_name}' "
            f"non suggerisce un default)",
            hint="Specifica --to <writer>, imposta 'defaults.writer' in config, "
                 "oppure usa un reader che dichiara un default_writer",
            path=str(path), reader_name=reader_name, **kw,
        )


class InvalidCliOptionError(ConfigError):
    def __init__(self, raw: str, **kw):
        super().__init__(
            f"Opzione non valida: '{raw}'",
            hint="Formato atteso: --opt chiave=valore",
            raw=raw, **kw,
        )


class DuplicateTableNameError(ConfigError):
    """Due o più sorgenti condividono lo stesso nome tabella (filename
    stem): build output, golden e history sono tutti indicizzati per
    nome, quindi una collisione sovrascriverebbe silenziosamente."""

    def __init__(self, duplicates: dict, **kw):
        lines = [f"'{name}': {', '.join(str(p) for p in paths)}" for name, paths in duplicates.items()]
        super().__init__(
            f"{len(duplicates)} nomi tabella duplicati",
            hint="Rinomina i file: i nomi tabella devono essere unici in tutto il "
                 "progetto (usati per build/golden/history)\n" + "\n".join(lines),
            duplicates={name: [str(p) for p in paths] for name, paths in duplicates.items()},
            **kw,
        )


# --- Exit code 3: golden --------------------------------------------------

class GoldenError(PayloadError):
    exit_code = 3


class GoldenMismatchError(GoldenError):
    """Sorgente invariato rispetto allo snapshot golden, ma l'output
    attuale ha bytes diversi — una vera regressione, il caso per cui
    golden esiste."""

    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"L'output di '{table_name}' non corrisponde allo snapshot golden",
            hint=(
                "Esegui 'pld golden diff <tabella>' per vedere le differenze, "
                "o 'pld commit --golden' se il cambio è intenzionale"
            ),
            table=table_name,
            **kw,
        )


class GoldenStaleError(GoldenError):
    """Il SORGENTE è cambiato rispetto allo snapshot golden — il
    confronto sull'output non è più affidabile (potrebbe combaciare per
    caso, o no): prima bisogna ricommittare per sapere se il nuovo
    output è ancora corretto."""

    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"Il sorgente di '{table_name}' è cambiato dopo l'ultimo golden: il confronto non è affidabile",
            hint="Esegui 'pld commit --golden' per aggiornare golden allo stato attuale, se è quello corretto",
            table=table_name,
            **kw,
        )


class GoldenMissingError(GoldenError):
    log_level = logging.WARNING

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"Nessun golden impostato per '{table_name}'",
            hint="Esegui 'pld golden set <tabella>' per impostarlo (richiede almeno uno snapshot già salvato)",
            table=table_name,
            **kw,
        )


# --- Exit code 4: file / reader / writer non trovato -----------------------

class NotFoundError(PayloadError):
    exit_code = 4


class SourceNotFoundError(NotFoundError):
    def __init__(self, path: Path, **kw):
        super().__init__(f"File sorgente non trovato: '{path}'", path=str(path), **kw)


class NoReaderFoundError(NotFoundError):
    def __init__(self, path: Path, **kw):
        super().__init__(
            f"Nessun reader trovato per '{path}'",
            hint="Usa 'pld plugins list' per vedere i formati supportati",
            path=str(path),
            **kw,
        )


class NoWriterFoundError(NotFoundError):
    def __init__(self, name: str, **kw):
        super().__init__(
            f"Writer '{name}' non registrato",
            hint="Usa 'pld plugins list' per vedere i writer disponibili",
            name=name,
            **kw,
        )


class TableNotFoundError(NotFoundError):
    """Nessun sorgente scoperto con questo nome tabella (stem) — usato
    dai comandi/route che cercano una tabella per nome invece che per
    path esplicito (es. 'pld config show <tabella>', le route web)."""

    def __init__(self, name: str, **kw):
        super().__init__(f"Tabella '{name}' non trovata", name=name, **kw)


class PluginNotFoundError(NotFoundError):
    """Nessun reader/writer/doctor-check registrato con questo nome —
    usato dai comandi/route che cercano un plugin per nome (es. 'pld
    plugin info <nome>', le route web)."""

    def __init__(self, name: str, **kw):
        super().__init__(
            f"Plugin '{name}' non trovato",
            hint="Usa 'pld plugins' per vedere quelli disponibili",
            name=name,
            **kw,
        )


# --- Exit code 5: history ---------------------------------------------------

class HistoryError(PayloadError):
    exit_code = 5


class SnapshotNotFoundError(HistoryError):
    def __init__(self, target, reason: str, **kw):
        super().__init__(
            f"Snapshot non trovato per '{target}': {reason}",
            hint="Usa 'pld log <tabella>' per vedere gli snapshot disponibili",
            target=str(target),
            **kw,
        )


class NothingToCommitError(HistoryError):
    log_level = logging.INFO

    def __init__(self, **kw):
        super().__init__(
            "Nessuna tabella modificata da salvare",
            hint="Usa 'pld status' per vedere lo stato attuale",
            **kw,
        )


class TableNotTrackedError(HistoryError):
    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"'{table_name}' non è mai stata salvata con 'pld commit'",
            table_name=table_name,
            **kw,
        )
