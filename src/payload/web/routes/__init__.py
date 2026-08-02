"""Aggregates the routes from every module in routes/ into a single
list that app.py mounts on the Starlette app. One module per resource
group, the same grouping already used by the commented sections in
cli.py."""
from __future__ import annotations

from starlette.routing import BaseRoute

from payload.web.routes import (
    batch,
    build,
    clusters,
    config,
    docs,
    fs,
    golden,
    history,
    local_plugin_editor,
    misc,
    plugins,
    source_editor,
    table_admin,
)

ROUTES: list[BaseRoute] = [
    *batch.ROUTES,
    *build.ROUTES,
    *history.ROUTES,
    *config.ROUTES,
    *golden.ROUTES,
    *plugins.ROUTES,
    *misc.ROUTES,
    *docs.ROUTES,
    *local_plugin_editor.ROUTES,
    *source_editor.ROUTES,
    *table_admin.ROUTES,
    *clusters.ROUTES,
    *fs.ROUTES,
]
