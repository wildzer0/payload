"""
Interfacce che ogni plugin (reader, writer, doctor check) deve rispettare.

Ogni plugin dichiara `api_version` (stringa "MAJOR.MINOR" allineata a
PLUGIN_API_VERSION). Il registry verifica la compatibilità al caricamento
e solleva PluginApiVersionError se non combacia il MAJOR.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from payload.core.ir import TableIR


@runtime_checkable
class Reader(Protocol):
    name: str
    extensions: list[str]
    api_version: str

    # Opzionale. Se impostato, usato come writer di default quando
    # né --to né config.defaults.writer specificano nulla — evita di
    # dover ripetere --to ogni volta per un reader con un formato di
    # output naturale/preferito. None = nessun suggerimento.
    default_writer: str | None

    def sniff(self, path: Path) -> bool:
        """Fallback di riconoscimento a contenuto, usato solo in caso di
        ambiguità tra più reader che matchano la stessa estensione."""
        ...  # pragma: no cover - corpo di Protocol, mai eseguito (non instanziabile)

    def parse(self, path: Path, config: dict) -> TableIR:
        ...  # pragma: no cover - corpo di Protocol, mai eseguito (non instanziabile)


@runtime_checkable
class Writer(Protocol):
    name: str
    extension: str
    api_version: str

    # Opzionale. Se impostato, il writer si applica SOLO a queste
    # source_format (nomi di reader) — combinazioni non elencate
    # sollevano WriterEmitError invece di produrre output silenziosamente
    # sbagliato. None = compatibile con qualsiasi reader (comportamento
    # di default per writer che serializzano bytes senza interpretarli,
    # es. bin/hex).
    compatible_readers: list[str] | None

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        ...  # pragma: no cover - corpo di Protocol, mai eseguito (non instanziabile)


class CheckStatus:
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class CheckResult:
    __slots__ = ("name", "status", "message", "hint")

    def __init__(self, name: str, status: str, message: str, hint: str | None = None):
        self.name = name
        self.status = status
        self.message = message
        self.hint = hint


@runtime_checkable
class DoctorCheck(Protocol):
    name: str
    api_version: str

    def run(self, config: dict) -> CheckResult:
        ...  # pragma: no cover - corpo di Protocol, mai eseguito (non instanziabile)


def check_api_compatibility(plugin_name: str, plugin_api_version: str) -> None:
    """Confronta solo il MAJOR: minor diversi restano compatibili (additive)."""
    from payload.core.errors import PluginApiVersionError
    from payload.core.ir import PLUGIN_API_VERSION

    plugin_major = plugin_api_version.split(".")[0]
    core_major = PLUGIN_API_VERSION.split(".")[0]
    if plugin_major != core_major:
        raise PluginApiVersionError(plugin_name, plugin_api_version, PLUGIN_API_VERSION)
