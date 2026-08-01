"""Aggrega le route di ogni modulo in routes/ in un'unica lista che
app.py monta sulla Starlette app. Un modulo per gruppo di risorse,
stessa suddivisione delle sezioni commentate già presenti in cli.py."""
from __future__ import annotations

from starlette.routing import BaseRoute

from payload.web.routes import build, config, docs, golden, history, local_plugin_editor, misc, plugins, source_editor

ROUTES: list[BaseRoute] = [
    *build.ROUTES,
    *history.ROUTES,
    *config.ROUTES,
    *golden.ROUTES,
    *plugins.ROUTES,
    *misc.ROUTES,
    *docs.ROUTES,
    *local_plugin_editor.ROUTES,
    *source_editor.ROUTES,
]
