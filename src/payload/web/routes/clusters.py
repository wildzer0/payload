"""Cluster CRUD + per-table tag/cluster assignment — web counterpart
of the 'pld cluster'/'pld tag' commands in cli.py, same split of
responsibilities. See src/payload/docs/CLUSTERS.md."""
from __future__ import annotations

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from payload.core.clusters import resolve_clusters
from payload.core.config import (
    config_schema,
    create_cluster,
    delete_cluster,
    load_config,
    set_table_cluster,
    set_table_tags,
    update_cluster,
)
from payload.core.table_meta import resolve_table_meta
from payload.web.errors import InvalidRequestError


def _cluster_payload(cluster, table_metas) -> dict:
    members = sorted(m.name for m in table_metas.values() if m.cluster == cluster.name)
    return {
        "name": cluster.name,
        "defaults": cluster.defaults,
        "plugin": cluster.plugin,
        "member_count": len(members),
        "members": members,
    }


async def clusters_list(request: Request) -> JSONResponse:
    root = request.app.state.root

    def _run():
        base_config = load_config(root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        return {
            "clusters": [_cluster_payload(c, table_metas) for c in clusters.values()],
            "schema": config_schema(),
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def cluster_create_route(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    if not name:
        raise InvalidRequestError("missing 'name' parameter")
    defaults = body.get("defaults") or {}
    plugin = body.get("plugin") or {}
    root = request.app.state.root

    def _run():
        create_cluster(root, name, defaults=defaults, plugin=plugin)
        base_config = load_config(root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        return _cluster_payload(clusters[name], table_metas)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def cluster_update_route(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    body = await request.json()
    root = request.app.state.root
    # 'defaults'/'plugin' absent from the body -> None (untouched);
    # present -> whatever dict was sent, {} clears that section — same
    # None-vs-{} convention as core/config.py's update_cluster/
    # write_sidecar_config.
    defaults = body["defaults"] if "defaults" in body else None
    plugin = body["plugin"] if "plugin" in body else None

    def _run():
        update_cluster(root, name, defaults=defaults, plugin=plugin)
        base_config = load_config(root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        return _cluster_payload(clusters[name], table_metas)

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def cluster_delete_route(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    force = request.query_params.get("force") == "true"
    root = request.app.state.root

    def _run():
        removed = delete_cluster(root, name, force=force)
        return {"name": name, "status": "deleted" if removed else "not_found"}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def table_cluster_route(request: Request) -> JSONResponse:
    table_name = request.path_params["table_name"]
    body = await request.json()
    if "cluster" not in body:
        raise InvalidRequestError("missing 'cluster' parameter")
    cluster = body["cluster"]
    if cluster is not None and not isinstance(cluster, str):
        raise InvalidRequestError("'cluster' must be a string or null")
    root = request.app.state.root

    def _run():
        set_table_cluster(root, table_name, cluster)
        return {"table_name": table_name, "cluster": cluster}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def table_tags_get_route(request: Request) -> JSONResponse:
    table_name = request.path_params["table_name"]
    root = request.app.state.root

    def _run():
        base_config = load_config(root)
        clusters = resolve_clusters(root, base_config)
        table_metas = resolve_table_meta(root, base_config, clusters)
        meta = table_metas.get(table_name)
        return {"tags": meta.tags if meta else []}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def table_tags_put_route(request: Request) -> JSONResponse:
    table_name = request.path_params["table_name"]
    body = await request.json()
    tags = body.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise InvalidRequestError("'tags' must be a list of strings")
    root = request.app.state.root

    def _run():
        set_table_tags(root, table_name, tags)
        return {"table_name": table_name, "tags": tags}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


ROUTES = [
    Route("/api/clusters", clusters_list, methods=["GET"]),
    Route("/api/clusters", cluster_create_route, methods=["POST"]),
    Route("/api/clusters/{name}", cluster_update_route, methods=["PUT"]),
    Route("/api/clusters/{name}", cluster_delete_route, methods=["DELETE"]),
    Route("/api/table/{table_name}/cluster", table_cluster_route, methods=["PUT"]),
    Route("/api/table/{table_name}/tags", table_tags_get_route, methods=["GET"]),
    Route("/api/table/{table_name}/tags", table_tags_put_route, methods=["PUT"]),
]
