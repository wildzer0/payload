"""
HTTP equivalent of 'run_command' in cli.py: a single central place that
converts every PayloadError into a JSON response with the right HTTP
status, and every unexpected exception into a generic 500 (never a
traceback exposed to the client, unlike the terminal where
err_console.print_exception() is intentional).

Registered ONCE on Starlette(exception_handlers=...) in app.py —
Starlette resolves by MRO, so registering PayloadError automatically
catches every subclass, exactly like the single 'except PayloadError'
in run_command today.

NOTE: this mechanism only protects normal JSON routes. The build-all
SSE route has already sent headers/status by the time the generator
starts producing output — an exception mid-stream can no longer become
a different HTTP status, so it handles its own errors internally with
an 'event: error' frame (see routes/build.py)."""
from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from payload.core.errors import (
    GoldenMissingError,
    NoOutputToCommitError,
    NothingToCommitError,
    PayloadError,
    PluginApiVersionError,
    PluginLoadError,
)

logger = logging.getLogger("payload.web")


class InvalidRequestError(PayloadError):
    """Malformed HTTP request (missing/invalid parameter) — not part
    of the core error hierarchy (core/errors.py), it's specific to the
    web layer: still routed through the same payload_error_handler, so
    it has the same JSON shape as every other error sent to the
    frontend."""

    exit_code = 2  # -> 400, same treatment as ConfigError


class DocNotFoundError(PayloadError):
    """Unknown documentation slug — 'documentation' isn't a core
    concept (no equivalent CLI command), so it lives here and not in
    core/errors.py."""

    exit_code = 4  # -> 404, same treatment as NotFoundError

    def __init__(self, slug: str, **kw):
        super().__init__(f"Document '{slug}' not found", slug=slug, **kw)


class NoBuildOutputError(PayloadError):
    """No output file on disk for this table — 'pld build' was never
    run, or the output was cleaned ('pld clean'). No direct CLI
    equivalent (downloading the blob is a web-only concept), so it
    lives here and not in core/errors.py."""

    exit_code = 4  # -> 404, same treatment as NotFoundError

    def __init__(self, table_name: str, **kw):
        super().__init__(
            f"No build output found for '{table_name}'",
            hint="Run a build before downloading the output",
            table_name=table_name,
            **kw,
        )


class LocalPluginFileNotFoundError(PayloadError):
    """Unknown file in plugins/ — used by the web editor
    (reading/writing/testing a single file), has no direct CLI
    equivalent so it lives here and not in core/errors.py."""

    exit_code = 4  # -> 404, same treatment as NotFoundError

    def __init__(self, filename: str, **kw):
        super().__init__(f"File '{filename}' not found in plugins/", filename=filename, **kw)

# The CLI exit codes (1-5, see core/errors.py) are already a grouping
# by category — the default mapping reuses that semantics instead of
# reinventing a new one.
_STATUS_BY_EXIT_CODE = {
    1: 422,  # BuildError: well-formed request, but the table can't be processed as-is
    2: 400,  # ConfigError: malformed input (config/pipeline/opt)
    3: 409,  # GoldenError: conflict between output and saved reference
    4: 404,  # NotFoundError: source/reader/writer/plugin doesn't exist
    5: 404,  # HistoryError: snapshot/table not found
}

# Overrides for subclasses whose HTTP meaning differs from their base
# class — checked BEFORE the generic map, the first matching
# isinstance() wins.
_STATUS_OVERRIDES: list[tuple[type[PayloadError], int]] = [
    (GoldenMissingError, 404),      # GoldenError->409 by default, but "missing" is closer to 404
    (NothingToCommitError, 409),    # HistoryError->404 by default, but this is a conflict state, not a 404
    (NoOutputToCommitError, 409),   # same reason: precondition not met, not a 404
    (PluginLoadError, 500),         # ConfigError->400 by default, but it's not the HTTP request's fault
    (PluginApiVersionError, 500),
]


def _status_for(exc: PayloadError) -> int:
    for cls, status in _STATUS_OVERRIDES:
        if isinstance(exc, cls):
            return status
    return _STATUS_BY_EXIT_CODE.get(exc.exit_code, 500)


async def payload_error_handler(request: Request, exc: PayloadError) -> JSONResponse:
    logger.log(exc.log_level, "%s %s -> %s: %s", request.method, request.url.path, type(exc).__name__, exc.message)
    return JSONResponse(exc.to_dict(), status_code=_status_for(exc))


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unexpected internal error: %s %s", request.method, request.url.path)
    return JSONResponse({"error": "InternalError", "message": "Unexpected internal error"}, status_code=500)


EXCEPTION_HANDLERS = {
    PayloadError: payload_error_handler,
    Exception: unexpected_error_handler,
}
