"""Project file browser — the 'never touch the folder' surface.

Every /api/fs/* route accepts free-form paths from the client, which is
different from every other route (whose inputs are table names, config
keys, ...): each of them therefore goes through
resolve_contained() (web/paths.py), which refuses anything escaping the
served project root — path traversal and symlink escapes included. This
is the single most important safety property of this module.

The default tree view hides the project's infrastructure (hidden
files/dirs, plugins/, output_dir, cache_dir — the same things
is_table_candidate() ignores for table discovery) unless
show_internal=true is passed, so a normal user sees only their content.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from payload.core.activity import log_event
from payload.core.config import SIDECAR_SUFFIX, load_config
from payload.core.discovery import all_table_refs, discover_for_history_lenient
from payload.core.file_ops import _is_internal_path, analyze_file, compare_files, search_files
from payload.web.errors import InvalidRequestError
from payload.web.paths import resolve_contained

# Never read more than this for a preview — the file browser must not
# try to load a multi-GB log into memory just to show its first page.
READ_CAP = 4 * 1024 * 1024
# Read cap for the text editor (beyond this a text file is shown
# truncated and marked as such, so a save can't silently clobber the
# rest of the file).
MAX_TEXT_CONTENT = 512 * 1024
# Per-page byte limit for the hex view.
MAX_HEX_LIMIT = 4096
HEX_ROW_BYTES = 16

# Extensions that are ALWAYS shown as hex, even when the bytes happen
# to decode as UTF-8 (a .bin full of printable bytes must not open in
# the text editor, where a save would re-encode and corrupt it).
BINARY_EXTENSIONS = {
    ".bin", ".o", ".obj", ".elf", ".img", ".dat", ".gz", ".zip", ".tar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf",
    ".ttf", ".woff", ".woff2", ".exe", ".so", ".dll", ".dylib", ".a",
}


def _is_binary_ext(p: Path) -> bool:
    return p.suffix.lower() in BINARY_EXTENSIONS

INTERNAL_DIR_NAMES = ("plugins",)  # plus output_dir/cache_dir, resolved per project


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - containment already guarantees p is under root
        return "."


def _table_context(root: Path) -> dict:
    """Table context for the fs layer, computed leniently: a project
    already broken by hand keeps working, and the uniqueness guards
    still prevent adding MORE conflicts. Returns:
      by_name       table name -> its source path (first one for dupes)
      by_source     resolved source path -> table name
      batch_members resolved paths of [[batch_table]] member files"""
    try:
        sources, batch_tables, _, _ = discover_for_history_lenient(root)
    except Exception:
        return {"by_name": {}, "by_source": {}, "batch_members": set()}
    by_name: dict[str, str] = {}
    by_source: dict[str, str] = {}
    batch_members: set[str] = set()
    for ref in all_table_refs(sources, batch_tables):
        by_name.setdefault(ref.name, str(ref.source_paths[0].resolve()))
        for sp in ref.source_paths:
            by_source[str(sp.resolve())] = ref.name
        if ref.is_batch:
            batch_members.update(str(sp.resolve()) for sp in ref.source_paths)
    return {"by_name": by_name, "by_source": by_source, "batch_members": batch_members}


def _stem_collision(root: Path, stem: str, new_path: Path, old_path: Path | None) -> str | None:
    """A table name must be unique across the whole project (it's the
    identity used by build/history/golden/cache). Returns a message when
    'stem' is already the name of a DIFFERENT table than the one at
    old_path — the table's own source may be renamed/moved freely (the
    same table just moves), and writing to the owner's own path is
    always allowed. None = no collision."""
    owner = _table_context(root)["by_name"].get(stem)
    if not owner:
        return None
    if old_path is not None and str(old_path) == owner:
        return None
    if str(new_path) == owner:
        return None
    return f"table name '{stem}' already used by '{_rel(root, Path(owner))}' — table names must be unique across the project"


def _entries_for(root: Path, target: Path, show_internal: bool) -> list[dict]:
    # the file browser is also the recovery tool: an invalid config must
    # not lock it out (that's the state you open it for), so the
    # output/cache exclusions fall back to the defaults when the config
    # can't even be parsed
    excluded_top = set(INTERNAL_DIR_NAMES)
    try:
        config = load_config(root)
        if config.defaults.output_dir:
            excluded_top.add(config.defaults.output_dir)
        if config.defaults.cache_dir:
            excluded_top.add(config.defaults.cache_dir)
    except Exception:
        excluded_top.update(("build", ".payload_cache"))
    is_root = target == root

    # best-effort table context: a broken config must NOT break the file
    # browser (it's also the recovery tool for such a project), so any
    # discovery error degrades to 'no context' instead of failing
    ctx = _table_context(root)

    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), _natural_key(x.name))):
        name = p.name
        if name.startswith(".") and not show_internal:
            continue
        if not show_internal and is_root and name in excluded_top:
            continue
        try:
            st = p.stat()
        except OSError:  # pragma: no cover - raced with a concurrent delete
            continue
        is_dir = p.is_dir()
        resolved = str(p.resolve())
        table_name = None if is_dir else ctx["by_source"].get(resolved)
        is_batch_member = table_name is not None and resolved in ctx["batch_members"]
        sidecar_table = None
        if table_name is None and not is_dir and name.endswith(SIDECAR_SUFFIX):
            stem = name[: -len(SIDECAR_SUFFIX)]
            if stem in ctx["by_name"]:
                sidecar_table = stem
        entries.append({
            "name": name,
            "is_dir": is_dir,
            "size": None if is_dir else st.st_size,
            "mtime": st.st_mtime,
            "table_name": table_name,
            "is_batch_member": is_batch_member,
            "sidecar_table": sidecar_table,
        })
    return entries


async def fs_tree(request: Request) -> JSONResponse:
    root = request.app.state.root.resolve()
    rel = request.query_params.get("path") or "."
    show_internal = request.query_params.get("show_internal") == "true"

    def _run():
        target = resolve_contained(root, rel)
        if not target.is_dir():
            raise InvalidRequestError(f"'{rel}' is not a directory")
        return {
            "path": _rel(root, target),
            "is_root": target == root,
            "entries": _entries_for(root, target, show_internal),
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_read(request: Request) -> JSONResponse:
    root = request.app.state.root.resolve()
    rel = request.query_params.get("path")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")

    def _run():
        p = resolve_contained(root, rel)
        if not p.is_file():
            raise InvalidRequestError(f"'{rel}' is not a file")
        size = p.stat().st_size

        capped = size > READ_CAP
        with p.open("rb") as f:
            data = f.read(READ_CAP if capped else size) if size else b""

        # extension wins over content: a known-binary extension (or an
        # explicit ?as_hex=1 request) always gets the hex view, even if
        # the bytes would decode as UTF-8
        as_hex = request.query_params.get("as_hex") == "1"
        text = None
        if not as_hex and not _is_binary_ext(p):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None

        if text is not None:
            truncated = len(text) > MAX_TEXT_CONTENT
            return {
                "path": _rel(root, p), "name": p.name, "size": size,
                "is_text": True, "content": text[:MAX_TEXT_CONTENT],
                "truncated": truncated,
            }

        try:
            offset = max(0, int(request.query_params.get("offset") or 0))
            limit = max(1, min(int(request.query_params.get("limit") or 256), MAX_HEX_LIMIT))
        except ValueError:
            raise InvalidRequestError("'offset'/'limit' must be integers")

        # hex branch: also report whether the bytes WOULD have decoded as
        # text — so the client can offer "View as text" back (a text file
        # shown as hex via ?as_hex=1 is a toggle, not a dead end)
        can_view_as_text = False
        if not _is_binary_ext(p):
            try:
                data.decode("utf-8")
                can_view_as_text = True
            except UnicodeDecodeError:
                can_view_as_text = False

        with p.open("rb") as f:
            f.seek(offset)
            chunk = f.read(limit)
        rows = []
        for i in range(0, len(chunk), HEX_ROW_BYTES):
            row = chunk[i:i + HEX_ROW_BYTES]
            rows.append({
                "offset": offset + i,
                "hex": " ".join(f"{b:02X}" for b in row),
                "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in row),
            })
        return {
            "path": _rel(root, p), "name": p.name, "size": size,
            "is_text": False, "rows": rows, "offset": offset,
            "end_offset": offset + len(chunk),
            "limit": limit, "has_more": offset + len(chunk) < size and offset + len(chunk) < READ_CAP,
            "can_view_as_text": can_view_as_text,
        }

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_write(request: Request) -> JSONResponse:
    body = await request.json()
    rel = body.get("path")
    content = body.get("content")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")
    if not isinstance(content, str):
        raise InvalidRequestError("'content' must be a string")
    root = request.app.state.root.resolve()

    def _run():
        p = resolve_contained(root, rel)
        if p.is_dir():
            raise InvalidRequestError(f"'{rel}' is a directory")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        log_event(root, "fs", f"edited '{rel}'")
        return {"path": _rel(root, p), "size": p.stat().st_size}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_create(request: Request) -> JSONResponse:
    body = await request.json()
    rel = body.get("path")
    kind = body.get("type", "file")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")
    if kind not in ("file", "dir"):
        raise InvalidRequestError("'type' must be 'file' or 'dir'")
    root = request.app.state.root.resolve()

    def _run():
        p = resolve_contained(root, rel)
        if p.exists():
            raise InvalidRequestError(f"'{rel}' already exists")
        if kind == "dir":
            p.mkdir(parents=True)
        else:
            # a new file becomes a table source: it must not steal an
            # existing table's name (unique across the project)
            collision = _stem_collision(root, p.stem, p, None)
            if collision:
                raise InvalidRequestError(collision)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="utf-8")
        log_event(root, "fs", f"created {kind} '{rel}'")
        return {"path": _rel(root, p), "type": kind}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_rename(request: Request) -> JSONResponse:
    """Rename within the same folder, or move across folders — both are
    'old path -> new path', the destination decides which one it is."""
    body = await request.json()
    rel = body.get("path")
    new_rel = body.get("new_path")
    if not rel or not new_rel:
        raise InvalidRequestError("missing 'path'/'new_path' parameter")
    root = request.app.state.root.resolve()

    def _run():
        old = resolve_contained(root, rel)
        new = resolve_contained(root, new_rel)
        if old == root:
            raise InvalidRequestError("refusing to rename the project root")
        if not old.exists():
            raise InvalidRequestError(f"'{rel}' doesn't exist")
        if new.exists():
            raise InvalidRequestError(f"'{new_rel}' already exists")
        if not new.parent.exists():
            raise InvalidRequestError(f"destination folder '{_rel(root, new.parent)}' doesn't exist")
        collision = _stem_collision(root, new.stem, new, old)
        if collision:
            raise InvalidRequestError(collision)
        old.rename(new)
        log_event(root, "fs", f"renamed '{rel}' → '{new_rel}'")
        return {"from": _rel(root, old), "path": _rel(root, new)}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_copy(request: Request) -> JSONResponse:
    body = await request.json()
    rel = body.get("path")
    new_rel = body.get("new_path")
    if not rel or not new_rel:
        raise InvalidRequestError("missing 'path'/'new_path' parameter")
    root = request.app.state.root.resolve()

    def _run():
        src = resolve_contained(root, rel)
        dst = resolve_contained(root, new_rel)
        if not src.exists():
            raise InvalidRequestError(f"'{rel}' doesn't exist")
        if dst.exists():
            raise InvalidRequestError(f"'{new_rel}' already exists")
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            collision = _stem_collision(root, dst.stem, dst, None)
            if collision:
                raise InvalidRequestError(collision)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        log_event(root, "fs", f"copied '{rel}' → '{new_rel}'")
        return {"from": _rel(root, src), "path": _rel(root, dst)}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_delete(request: Request) -> JSONResponse:
    """Same preview/confirm split as /api/table/delete and /api/restore:
    without 'confirm' it reports what would be deleted, with it the
    deletion actually happens."""
    body = await request.json()
    rel = body.get("path")
    confirm = bool(body.get("confirm", False))
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")
    root = request.app.state.root.resolve()

    def _run():
        p = resolve_contained(root, rel)
        if p == root:
            raise InvalidRequestError("refusing to delete the project root")
        if not p.exists():
            raise InvalidRequestError(f"'{rel}' doesn't exist")
        count = (len(list(p.rglob("*"))) + 1) if p.is_dir() else 1
        if not confirm:
            return {"status": "confirmation_required", "path": _rel(root, p), "is_dir": p.is_dir(), "entries": count}
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        log_event(root, "fs", f"deleted '{rel}' ({count} entr{'y' if count == 1 else 'ies'})")
        return {"status": "deleted", "path": _rel(root, p), "entries": count}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_upload(request: Request) -> JSONResponse:
    """multipart/form-data: 'dir' (target folder, relative) + one or more
    'file' fields. Filenames are sanitized to their basename (a client
    can't smuggle path separators through the upload name)."""
    root = request.app.state.root.resolve()
    form = await request.form()
    dir_rel = (form.get("dir") or "").strip() or "."
    overwrite = form.get("overwrite") == "true"
    uploads = form.getlist("file")
    if not uploads:
        raise InvalidRequestError("no file uploaded (missing 'file' field)")

    def _run():
        target = resolve_contained(root, dir_rel)
        if not target.is_dir():
            raise InvalidRequestError(f"'{dir_rel}' is not a directory")
        imported: list[str] = []
        skipped: list[dict] = []
        for u in uploads:
            if not hasattr(u, "filename"):
                # a multipart part under the 'file' name without a
                # filename is a plain form field, not an upload
                skipped.append({"name": "", "reason": "empty filename"})
                continue
            name = Path(u.filename or "").name  # strips any directory part
            if not name:  # pragma: no cover - python-multipart yields a plain field, not a file, for empty names
                skipped.append({"name": u.filename, "reason": "empty filename"})
                continue
            dest = target / name
            # a file whose stem collides with an existing table name
            # would break discovery project-wide — refuse it up front
            collision = _stem_collision(root, Path(name).stem, dest, None)
            if collision:
                skipped.append({"name": name, "reason": collision})
                continue
            if dest.exists() and not overwrite:
                skipped.append({"name": name, "reason": "already exists"})
                continue
            dest.write_bytes(u.file.read())
            imported.append(_rel(root, dest))
        if imported:
            log_event(root, "fs", f"uploaded {len(imported)} file(s) into '{dir_rel}'")
        return {"imported": imported, "skipped": skipped}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_strings(request: Request) -> JSONResponse:
    """ASCII strings found inside a file (printable runs of >= 4 chars)
    — the binary previewer's 'Strings' view, the quickest way to spot
    embedded text (labels, error messages, magic numbers) in a .bin
    without decoding the whole thing."""
    root = request.app.state.root.resolve()
    rel = request.query_params.get("path")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")

    def _run():
        p = resolve_contained(root, rel)
        if not p.is_file():
            raise InvalidRequestError(f"'{rel}' is not a file")
        size = p.stat().st_size
        capped = size > READ_CAP
        with p.open("rb") as f:
            data = f.read(READ_CAP if capped else size) if size else b""

        strings = []
        cur = bytearray()
        start = 0
        for i, b in enumerate(data):
            if 32 <= b < 127:
                if not cur:
                    start = i
                cur.append(b)
            else:
                if len(cur) >= 4:
                    strings.append({"offset": start, "text": cur.decode("ascii")})
                cur = bytearray()
        if len(cur) >= 4:
            strings.append({"offset": start, "text": cur.decode("ascii")})
        return {"path": _rel(root, p), "strings": strings, "capped": capped}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_compare(request: Request) -> JSONResponse:
    """Byte-level comparison of two files (see core/file_ops.py) — the
    'Compare any two files' feature, not tied to history/golden."""
    path_a = request.query_params.get("path_a")
    path_b = request.query_params.get("path_b")
    if not path_a or not path_b:
        raise InvalidRequestError("missing 'path_a'/'path_b' parameter")
    root = request.app.state.root.resolve()

    def _run():
        pa = resolve_contained(root, path_a)
        pb = resolve_contained(root, path_b)
        if not pa.is_file():
            raise InvalidRequestError(f"'{path_a}' is not a file")
        if not pb.is_file():
            raise InvalidRequestError(f"'{path_b}' is not a file")
        r = compare_files(pa, pb)
        r["a"] = _rel(root, pa)
        r["b"] = _rel(root, pb)
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_search(request: Request) -> JSONResponse:
    """Content search across the project (text or byte pattern) — the
    'grep in the browser' feature. Matches are project-relative so the
    frontend can jump straight to the file and offset."""
    q = request.query_params.get("q") or ""
    hexpat = request.query_params.get("hex") or ""
    rel_dir = request.query_params.get("path") or "."
    try:
        max_results = min(int(request.query_params.get("limit") or 50), 500)
    except ValueError:
        raise InvalidRequestError("'limit' must be an integer")
    if not q and not hexpat:
        raise InvalidRequestError("missing 'q' or 'hex'")
    if q and hexpat:
        raise InvalidRequestError("use either 'q' or 'hex', not both")
    root = request.app.state.root.resolve()

    def _run():
        target = resolve_contained(root, rel_dir)
        if not target.is_dir():
            raise InvalidRequestError(f"'{rel_dir}' is not a directory")
        if hexpat:
            clean = hexpat.replace(" ", "").replace("0x", "").replace(",", "")
            if len(clean) % 2:
                raise InvalidRequestError("hex pattern must have an even number of digits")
            try:
                pattern = bytes.fromhex(clean)
            except ValueError:
                raise InvalidRequestError("invalid hex pattern")
        else:
            pattern = q.encode("utf-8")
        r = search_files(root, pattern, max_results=max_results, start=target)
        r["query"] = q or hexpat
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_analyze(request: Request) -> JSONResponse:
    """Binary analysis: entropy / printable ratio / magic / byte
    frequency (see core/file_ops.py)."""
    rel = request.query_params.get("path")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")
    root = request.app.state.root.resolve()

    def _run():
        p = resolve_contained(root, rel)
        if not p.is_file():
            raise InvalidRequestError(f"'{rel}' is not a file")
        r = analyze_file(p)
        r["path"] = _rel(root, p)
        return r

    return JSONResponse(await anyio.to_thread.run_sync(_run))


MAX_LIST_FILES = 500


async def fs_list(request: Request) -> JSONResponse:
    """Every file under a folder (default: the whole project, internal
    dirs skipped) with project-relative paths — feeds the Compare file
    picker (and anything else that needs a quick file list)."""
    rel_dir = request.query_params.get("path") or "."
    root = request.app.state.root.resolve()

    def _run():
        target = resolve_contained(root, rel_dir)
        if not target.is_dir():
            raise InvalidRequestError(f"'{rel_dir}' is not a directory")
        files = []
        for p in sorted(target.rglob("*")):
            if not p.is_file():
                continue
            rel = _rel(root, p)
            if _is_internal_path(root, rel):
                continue
            files.append(rel)
            if len(files) >= MAX_LIST_FILES:
                return {"files": files, "truncated": True}
        return {"files": files, "truncated": False}

    return JSONResponse(await anyio.to_thread.run_sync(_run))


async def fs_download(request: Request) -> FileResponse:
    root = request.app.state.root.resolve()
    rel = request.query_params.get("path")
    if not rel:
        raise InvalidRequestError("missing 'path' parameter")

    def _run() -> Path:
        p = resolve_contained(root, rel)
        if not p.is_file():
            raise InvalidRequestError(f"'{rel}' is not a file")
        return p

    p = await anyio.to_thread.run_sync(_run)
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


ROUTES = [
    Route("/api/fs/tree", fs_tree, methods=["GET"]),
    Route("/api/fs/read", fs_read, methods=["GET"]),
    Route("/api/fs/write", fs_write, methods=["PUT"]),
    Route("/api/fs/create", fs_create, methods=["POST"]),
    Route("/api/fs/rename", fs_rename, methods=["POST"]),
    Route("/api/fs/copy", fs_copy, methods=["POST"]),
    Route("/api/fs/delete", fs_delete, methods=["POST"]),
    Route("/api/fs/upload", fs_upload, methods=["POST"]),
    Route("/api/fs/strings", fs_strings, methods=["GET"]),
    Route("/api/fs/compare", fs_compare, methods=["GET"]),
    Route("/api/fs/search", fs_search, methods=["GET"]),
    Route("/api/fs/analyze", fs_analyze, methods=["GET"]),
    Route("/api/fs/list", fs_list, methods=["GET"]),
    Route("/api/fs/download", fs_download, methods=["GET"]),
]
