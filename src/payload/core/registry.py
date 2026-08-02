"""
Plugin registry and discovery (reader, writer, doctor check) via
importlib.metadata entry_points. No external dependency needed:
entry_points has been native in stdlib since Python 3.10+.
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
        # track the originating entry point for 'plugins list' (package/version)
        self._origin: dict[str, EntryPoint] = {}
        # plugins that failed to load in non-strict mode, with the
        # reason — used by 'pld doctor' to report them by name instead
        # of a generic "discovery completed"
        self.load_failures: list[tuple[str, str, str]] = []  # (name, group, reason)

    def register_reader(self, r: Reader) -> None:
        if r.name in self.readers:
            logger.warning("Reader '%s' already registered, overwritten", r.name)
        self.readers[r.name] = r

    def register_writer(self, w: Writer) -> None:
        if w.name in self.writers:
            logger.warning("Writer '%s' already registered, overwritten", w.name)
        self.writers[w.name] = w

    def register_doctor_check(self, c: DoctorCheck) -> None:
        if c.name in self.doctor_checks:
            logger.warning("Doctor check '%s' already registered, overwritten", c.name)
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

    def is_installed(self, name: str) -> bool:
        """True if this plugin was loaded via a pip-installed
        entry_point (payload.readers/payload.writers/
        payload.doctor_checks, declared by ANY installed package — see
        pyproject.toml; payload itself declares none) rather than from
        the project's own plugins/ folder. Distinguishes "came from a
        pip package" from "the project wrote it" in the UI ('plugins
        list' groups them separately)."""
        return name in self._origin


def load_plugins(project_root: Path | None = None, strict: bool = True) -> PluginRegistry:
    """Loads every plugin: first the ones installed via entry_points
    (pip packages — none ship with payload itself, see pyproject.toml),
    then the project's own ones (loose .py files in 'plugins/' or in
    PAYLOAD_PLUGIN_PATH — see core/local_plugins.py).

    project_root: where to look for 'plugins/'. If None, uses the
    current working directory (sensible behavior for the vast majority
    of commands, which operate from the project folder).

    strict=True: a plugin that fails to load or has an incompatible
    API stops immediately with PluginLoadError/PluginApiVersionError
    (default behavior for build/watch/etc).
    strict=False: records the error and continues (used by 'doctor'
    and 'plugins list', which want to show *every* problem at once).
    """
    from payload.core.local_plugins import load_local_plugins

    registry = PluginRegistry()

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
                logger.debug("Plugin loaded: %s (%s)", ep.name, group)
            except Exception as e:
                logger.debug("Failed to load plugin %s (%s): %s", ep.name, group, e)
                if strict:
                    raise PluginLoadError(ep.name, group, str(e)) from e
                # in non-strict mode, we don't register the plugin but
                # we keep track of why: 'doctor' will report it by name
                registry.load_failures.append((ep.name, group, str(e)))

    load_local_plugins(project_root or Path.cwd(), registry, strict=strict)

    return registry
