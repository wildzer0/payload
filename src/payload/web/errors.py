"""
Equivalente HTTP di 'run_command' in cli.py: un solo punto centrale che
converte ogni PayloadError in una risposta JSON con lo status HTTP
giusto, e ogni eccezione imprevista in un 500 generico (mai un
traceback esposto al client, a differenza del terminale dove
err_console.print_exception() è voluto).

Registrato UNA VOLTA su Starlette(exception_handlers=...) in app.py —
Starlette risolve per MRO, quindi registrare PayloadError cattura
automaticamente ogni sottoclasse, esattamente come il singolo
'except PayloadError' di run_command oggi.

NOTA: questo meccanismo protegge solo le route JSON normali. Le route
SSE (build-all/watch) hanno già inviato gli header/status quando il
generatore comincia a produrre — un'eccezione a metà stream non può
più diventare uno status HTTP diverso, gestiscono quindi i propri
errori internamente con un frame 'event: error' (vedi routes/build.py
e watch_session.py)."""
from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from payload.core.errors import (
    GoldenMissingError,
    NothingToCommitError,
    PayloadError,
    PluginApiVersionError,
    PluginLoadError,
)

logger = logging.getLogger("payload.web")


class InvalidRequestError(PayloadError):
    """Richiesta HTTP malformata (parametro mancante/non valido) — non
    fa parte della gerarchia errori del core (core/errors.py), è
    specifica del layer web: instrada comunque nello stesso
    payload_error_handler, quindi lo stesso shape JSON di ogni altro
    errore verso il frontend."""

    exit_code = 2  # -> 400, stesso trattamento di ConfigError


class DocNotFoundError(PayloadError):
    """Slug di documentazione sconosciuto — 'documentazione' non è un
    concetto del core (niente comando CLI equivalente), quindi vive qui
    e non in core/errors.py."""

    exit_code = 4  # -> 404, stesso trattamento di NotFoundError

    def __init__(self, slug: str, **kw):
        super().__init__(f"Documento '{slug}' non trovato", slug=slug, **kw)


class LocalPluginFileNotFoundError(PayloadError):
    """File sconosciuto in local_plugins/ — usato dall'editor web
    (lettura/scrittura/test di un singolo file), non ha equivalente CLI
    diretto quindi vive qui e non in core/errors.py."""

    exit_code = 4  # -> 404, stesso trattamento di NotFoundError

    def __init__(self, filename: str, **kw):
        super().__init__(f"File '{filename}' non trovato in local_plugins/", filename=filename, **kw)

# Gli exit code CLI (1-5, vedi core/errors.py) sono già un raggruppamento
# per categoria — la mappatura di default riusa quella semantica invece
# di reinventarne una nuova.
_STATUS_BY_EXIT_CODE = {
    1: 422,  # BuildError: richiesta ben formata, ma la tabella non è processabile così com'è
    2: 400,  # ConfigError: input (config/pipeline/opt) malformato
    3: 409,  # GoldenError: conflitto tra output e riferimento salvato
    4: 404,  # NotFoundError: sorgente/reader/writer/plugin inesistente
    5: 404,  # HistoryError: snapshot/tabella non trovati
}

# Override per sottoclassi il cui significato HTTP si discosta da
# quello della classe base — controllati PRIMA della mappa generica,
# la prima isinstance() che matcha vince.
_STATUS_OVERRIDES: list[tuple[type[PayloadError], int]] = [
    (GoldenMissingError, 404),      # GoldenError->409 di default, ma "manca" è più simile a 404
    (NothingToCommitError, 409),    # HistoryError->404 di default, ma qui è uno stato di conflitto, non un 404
    (PluginLoadError, 500),         # ConfigError->400 di default, ma non è colpa della richiesta HTTP
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
    logger.exception("Errore interno inatteso: %s %s", request.method, request.url.path)
    return JSONResponse({"error": "InternalError", "message": "Errore interno inatteso"}, status_code=500)


EXCEPTION_HANDLERS = {
    PayloadError: payload_error_handler,
    Exception: unexpected_error_handler,
}
