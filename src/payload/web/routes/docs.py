"""User guides bundled with the package (payload.docs, see
[tool.setuptools.package-data] in pyproject.toml) — the same
documentation as src/payload/docs/*.md, browsable from 'pld serve'
without needing the source repository."""
from __future__ import annotations

import importlib.resources

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.web.errors import DocNotFoundError

DOCS = [
    {
        "slug": "usage", "filename": "USAGE.md", "title": "User guide",
        "description": "Every command with its options, the config file, exit codes, end-to-end workflows.",
    },
    {
        "slug": "howto", "filename": "HOWTO.md", "title": "HOW TO — guided tour",
        "description": "A step-by-step walkthrough of every operation, with the sample files in examples/howto/.",
    },
    {
        "slug": "developer", "filename": "DEVELOPER.md", "title": "Developer guide",
        "description": "Architecture, design and where to touch to maintain the code.",
    },
    {
        "slug": "plugins", "filename": "PLUGINS.md", "title": "Plugin guide",
        "description": "The full Reader/Writer/TableIR contract, explained with commented examples.",
    },
    {
        "slug": "pipeline", "filename": "PIPELINE.md", "title": "Configurable pipeline",
        "description": "The reader/writer/exec stages: how they work, the syntax, the examples.",
    },
    {
        "slug": "batch", "filename": "BATCH.md", "title": "Batch tables",
        "description": "A table built from several source files: [[batch_table]], parse_many, limits.",
    },
    {
        "slug": "clusters", "filename": "CLUSTERS.md", "title": "Clusters & tags",
        "description": "One cluster per table for shared config overrides, plus free-form tags for search/filtering.",
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
