"""Guide utente incluse nel pacchetto (payload.docs, vedi
[tool.setuptools.package-data] in pyproject.toml) — la stessa
documentazione di src/payload/docs/*.md, consultabile da 'pld serve'
senza bisogno del repository sorgente."""
from __future__ import annotations

import importlib.resources

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.web.errors import DocNotFoundError

DOCS = [
    {
        "slug": "usage", "filename": "USAGE.md", "title": "Guida utente",
        "description": "Ogni comando con le sue opzioni, il config file, i codici di uscita, i workflow end-to-end.",
    },
    {
        "slug": "plugins", "filename": "PLUGINS.md", "title": "Guida ai plugin",
        "description": "Il contratto Reader/Writer/TableIR spiegato per intero, con esempi commentati.",
    },
    {
        "slug": "pipeline", "filename": "PIPELINE.md", "title": "Pipeline configurabile",
        "description": "Gli stage reader/writer/exec: come funzionano, la sintassi, gli esempi.",
    },
    {
        "slug": "batch", "filename": "BATCH.md", "title": "Tabelle batch",
        "description": "Una tabella costruita da più file sorgente: [[batch_table]], parse_many, limiti.",
    },
]
_BY_SLUG = {d["slug"]: d for d in DOCS}


async def docs_list(request: Request) -> JSONResponse:
    return JSONResponse({
        "docs": [{"slug": d["slug"], "title": d["title"], "description": d["description"]} for d in DOCS],
    })


async def doc_detail(request: Request) -> JSONResponse:
    slug = request.path_params["slug"]
    doc = _BY_SLUG.get(slug)
    if doc is None:
        raise DocNotFoundError(slug)

    def _run():
        return (importlib.resources.files("payload.docs") / doc["filename"]).read_text(encoding="utf-8")

    content = await anyio.to_thread.run_sync(_run)
    return JSONResponse({"slug": slug, "title": doc["title"], "content": content})


ROUTES = [
    Route("/api/docs", docs_list, methods=["GET"]),
    Route("/api/docs/{slug}", doc_detail, methods=["GET"]),
]
