"""
Registro e discovery dei plugin (reader, writer, doctor check) via
importlib.metadata entry_points. Nessuna dipendenza esterna necessaria:
entry_points è nativo in stdlib da Python 3.10+.
"""
from __future__ import annotations

import logging
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from payload.core.errors import (
    AmbiguousReaderError,
    NoReaderFoundError,
    PluginLoadError,
)
from payload.core.plugin_base import DoctorCheck, Reader, Writer, check_api_compatibility

logger = logging.getLogger(__name__)

READER_GROUP = "payload.readers"
WRITER_GROUP = "payload.writers"
DOCTOR_GROUP = "payload.doctor_checks"


class PluginRegistry:
    def __init__(self) -> None:
        self.readers: dict[str, Reader] = {}
        self.writers: dict[str, Writer] = {}
        self.doctor_checks: dict[str, DoctorCheck] = {}
        # tracciamo l'entry point d'origine per 'plugins list' (pacchetto/versione)
        self._origin: dict[str, EntryPoint] = {}
        # plugin che sono falliti al caricamento in modalità non-strict,
        # con la ragione — usato da 'pld doctor' per riportarli per nome
        # invece di un generico "discovery completata"
        self.load_failures: list[tuple[str, str, str]] = []  # (name, group, reason)

    def register_reader(self, r: Reader) -> None:
        if r.name in self.readers:
            logger.warning("Reader '%s' già registrato, sovrascritto", r.name)
        self.readers[r.name] = r

    def register_writer(self, w: Writer) -> None:
        if w.name in self.writers:
            logger.warning("Writer '%s' già registrato, sovrascritto", w.name)
        self.writers[w.name] = w

    def register_doctor_check(self, c: DoctorCheck) -> None:
        if c.name in self.doctor_checks:
            logger.warning("Doctor check '%s' già registrato, sovrascritto", c.name)
        self.doctor_checks[c.name] = c

    def find_reader(self, path: Path, explicit: str | None = None) -> Reader:
        if explicit:
            if explicit not in self.readers:
                raise NoReaderFoundError(path)
            return self.readers[explicit]

        candidates = [r for r in self.readers.values() if path.suffix in r.extensions]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            sniffed = [r for r in candidates if r.sniff(path)]
            if len(sniffed) == 1:
                return sniffed[0]
            raise AmbiguousReaderError(path, [r.name for r in candidates])

        raise NoReaderFoundError(path)


def load_plugins(project_root: Path | None = None, strict: bool = True) -> PluginRegistry:
    """Carica tutti i plugin: prima quelli installati via entry_points
    (pacchetti pip), poi quelli locali (file .py sciolti in
    'local_plugins/' o in PAYLOAD_PLUGIN_PATH — vedi core/local_plugins.py).

    project_root: dove cercare 'local_plugins/'. Se None, usa la
    working directory corrente (comportamento sensato per la stragrande
    maggioranza dei comandi, che operano dalla cartella del progetto).

    strict=True: un plugin che fallisce a caricare o con API incompatibile
    interrompe subito con PluginLoadError/PluginApiVersionError (comportamento
    di default per build/watch/ecc.).
    strict=False: registra l'errore e prosegue (usato da 'doctor' e
    'plugins list', che vogliono mostrare *tutti* i problemi in un colpo solo).
    """
    from payload.core.builtin_plugins import is_frozen, register_builtin_plugins
    from payload.core.local_plugins import load_local_plugins

    registry = PluginRegistry()

    if is_frozen():
        # dentro un exe PyInstaller, entry_points() può non trovare il
        # dist-info correttamente anche se altre funzioni di
        # importlib.metadata funzionano — bypassiamo il problema del
        # tutto per i plugin che spediamo noi. Vedi core/builtin_plugins.py.
        register_builtin_plugins(registry)
        logger.debug("Processo congelato: plugin builtin registrati via import diretto")
    else:
        for group, register_fn in (
            (READER_GROUP, registry.register_reader),
            (WRITER_GROUP, registry.register_writer),
            (DOCTOR_GROUP, registry.register_doctor_check),
        ):
            for ep in entry_points(group=group):
                try:
                    cls = ep.load()
                    instance = cls()
                    check_api_compatibility(ep.name, getattr(instance, "api_version", "0.0"))
                    register_fn(instance)
                    registry._origin[ep.name] = ep
                    logger.debug("Plugin caricato: %s (%s)", ep.name, group)
                except Exception as e:
                    logger.debug("Fallito caricamento plugin %s (%s): %s", ep.name, group, e)
                    if strict:
                        raise PluginLoadError(ep.name, group, str(e)) from e
                    # in modalità non strict, non registriamo il plugin ma
                    # teniamo traccia del motivo: 'doctor' lo riporterà per nome
                    registry.load_failures.append((ep.name, group, str(e)))

    load_local_plugins(project_root or Path.cwd(), registry, strict=strict)

    return registry
