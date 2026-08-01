"""
Interfaces that every plugin (reader, writer, doctor check) must satisfy.

Every plugin declares `api_version` (a "MAJOR.MINOR" string aligned
with PLUGIN_API_VERSION). The registry checks compatibility at load
time and raises PluginApiVersionError if the MAJOR doesn't match.
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

    # Optional. If set, used as the default writer when neither --to
    # nor config.defaults.writer specify anything — avoids having to
    # repeat --to every time for a reader with a natural/preferred
    # output format. None = no suggestion.
    default_writer: str | None

    def sniff(self, path: Path) -> bool:
        """Content-based recognition fallback, used only when multiple
        readers match the same extension."""
        ...  # pragma: no cover - Protocol body, never executed (not instantiable)

    def parse(self, path: Path, config: dict) -> TableIR:
        ...  # pragma: no cover - Protocol body, never executed (not instantiable)


@runtime_checkable
class Writer(Protocol):
    name: str
    extension: str
    api_version: str

    # Optional. If set, the writer only applies to these source_format
    # values (reader names) — unlisted combinations raise
    # WriterEmitError instead of silently producing wrong output.
    # None = compatible with any reader (default behavior for writers
    # that serialize bytes without interpreting them, e.g. bin/hex).
    compatible_readers: list[str] | None

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        ...  # pragma: no cover - Protocol body, never executed (not instantiable)


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
        ...  # pragma: no cover - Protocol body, never executed (not instantiable)


def check_api_compatibility(plugin_name: str, plugin_api_version: str) -> None:
    """Compares only the MAJOR: different minors stay compatible (additive)."""
    from payload.core.errors import PluginApiVersionError
    from payload.core.ir import PLUGIN_API_VERSION

    plugin_major = plugin_api_version.split(".")[0]
    core_major = PLUGIN_API_VERSION.split(".")[0]
    if plugin_major != core_major:
        raise PluginApiVersionError(plugin_name, plugin_api_version, PLUGIN_API_VERSION)
