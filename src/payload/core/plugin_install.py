"""
'pld plugin install <source>': copies a single-file plugin (reader/
writer/doctor-check) into a project's plugins/ folder — from a local
path, or a direct http(s):// URL to a raw .py file (stdlib urllib
only, no new dependency, no 'git clone': v1 is single-file only, see
the module docstring in core/local_plugins.py for the file convention
a plugin must follow).

Explicit consent (overwrite) is the caller's responsibility (CLI/web),
same split as core/table_admin.py's import_* functions — this module
just performs the operation, it doesn't ask for confirmation itself.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from payload.core.errors import PluginAlreadyExistsError, PluginSourceNotFoundError
from payload.core.local_plugins import extract_plugin_classes, load_module_from_file

_URL_SCHEMES = ("http://", "https://")
_FETCH_TIMEOUT_SECONDS = 15


def _validate_filename(raw: str) -> str:
    """A plain .py filename, never a path — no separators, no '..',
    no empty/hidden name. Same shape as table_admin.py's
    _validate_filename / local_plugin_editor.py's _safe_filename:
    small per-module duplication is the existing convention for this
    check, not a shared cross-cutting utility."""
    if (
        not raw.endswith(".py")
        or "/" in raw or "\\" in raw
        or raw in (".", "..")
        or raw.startswith(".")
    ):
        raise PluginSourceNotFoundError(raw, "invalid filename (must be a plain '*.py' name)")
    return raw


def _fetch_url(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310 - http(s) enforced by _URL_SCHEMES check below
            return resp.read()
    except urllib.error.URLError as e:
        raise PluginSourceNotFoundError(url, str(e.reason if hasattr(e, "reason") else e)) from e


def _read_local(path: Path) -> bytes:
    if not path.is_file():
        raise PluginSourceNotFoundError(str(path), "no such file")
    return path.read_bytes()


@dataclass
class InstallResult:
    path: Path
    filename: str
    sanity_ok: bool
    sanity_issues: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)  # e.g. ["reader"], ["writer", "doctor_check"]


def _sanity_check(path: Path) -> tuple[bool, list[str], list[str]]:
    """Best-effort check right after install: does it parse, does it
    expose READER/WRITER/DOCTOR_CHECK. A failure here does NOT undo
    the write — it's reported back to the caller, same 'validate and
    report, don't silently undo' style as local_plugin_editor.py's
    /test endpoint (which also validates an already-saved file)."""
    try:
        module = load_module_from_file(path)
    except Exception as e:
        return False, [f"{type(e).__name__}: {e}"], []

    classes = extract_plugin_classes(module)
    if not classes:
        return False, ["no READER/WRITER/DOCTOR_CHECK declared in the file"], []

    kinds = sorted({kind for kind, _ in classes})
    return True, [], kinds


def _write_and_check(dest_dir: Path, filename: str, data: bytes, overwrite: bool) -> InstallResult:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    if dest_path.exists() and not overwrite:
        raise PluginAlreadyExistsError(dest_path)

    dest_path.write_bytes(data)

    sanity_ok, issues, kinds = _sanity_check(dest_path)
    return InstallResult(path=dest_path, filename=filename, sanity_ok=sanity_ok, sanity_issues=issues, kinds=kinds)


def install_plugin(
    dest_dir: Path,
    source: str,
    as_name: str | None = None,
    overwrite: bool = False,
) -> InstallResult:
    """source: a local filesystem path to a .py file, OR an http(s)://
    URL to a raw single .py file. Copies/downloads into dest_dir
    (created if missing), refusing to overwrite an existing file
    unless overwrite=True — same no-silent-overwrite principle as
    core/table_admin.py's import_single_table."""
    is_url = source.startswith(_URL_SCHEMES)

    if as_name is not None:
        filename = _validate_filename(as_name)
    elif is_url:
        url_name = Path(urlparse(source).path).name
        if not url_name:
            raise PluginSourceNotFoundError(source, "couldn't derive a filename from the URL — pass --as <name>.py")
        filename = _validate_filename(url_name)
    else:
        filename = _validate_filename(Path(source).name)

    data = _fetch_url(source) if is_url else _read_local(Path(source))
    return _write_and_check(dest_dir, filename, data, overwrite)


def install_plugin_from_bytes(
    dest_dir: Path,
    filename: str,
    data: bytes,
    overwrite: bool = False,
) -> InstallResult:
    """Same as install_plugin, but for content already in memory (a
    browser upload/drag&drop — see web/routes/plugins.py) instead of a
    path or URL to fetch: no source to resolve, just filename + bytes
    the caller already has."""
    filename = _validate_filename(filename)
    return _write_and_check(dest_dir, filename, data, overwrite)
