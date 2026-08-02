"""Test for the /api/fs/* file-browser routes (web/routes/fs.py):
containment (no path traversal), tree filtering, read/write, CRUD,
upload, download."""
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from payload.web.app import create_app


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root), raise_server_exceptions=False)


def _project(tmp_path: Path) -> Path:
    """A small project: a config, a table source, a sidecar, an output
    dir, a cache dir and a plugins/ folder."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    (root / "sensors").mkdir()
    (root / "sensors" / "temp.raw").write_text("payload data\n")
    (root / "sensors" / "temp.config.toml").write_text("[defaults]\nbyte_order = \"big\"\n")
    (root / "build").mkdir()
    (root / "build" / "temp.bin").write_bytes(b"\x00\x01")
    (root / "plugins").mkdir()
    (root / "plugins" / "my_reader.py").write_text("READER = None\n")
    (root / "bin.raw").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    return root


# ---------- tree ----------

def test_tree_lists_entries_dirs_first(tmp_path):
    client = _client(_project(tmp_path))
    r = client.get("/api/fs/tree")
    assert r.status_code == 200
    body = r.json()
    names = [(e["name"], e["is_dir"]) for e in body["entries"]]
    assert ("sensors", True) in names
    assert ("bin.raw", False) in names
    # dirs come before files, alphabetical within each group
    dirs = [n for n, d in names if d]
    files = [n for n, d in names if not d]
    assert dirs == sorted(dirs)
    assert files == sorted(files)


def test_tree_default_hides_internal_dirs(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    names = {e["name"] for e in client.get("/api/fs/tree").json()["entries"]}
    assert "sensors" in names
    assert "table-tool.toml" in names
    assert "build" not in names
    assert "plugins" not in names
    assert ".payload_cache" not in names


def test_tree_show_internal_reveals_them(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    names = {e["name"] for e in client.get("/api/fs/tree?show_internal=true").json()["entries"]}
    assert {"build", "plugins"} <= names


def test_tree_dotfiles_hidden_by_default_shown_with_toggle(tmp_path):
    root = _project(tmp_path)
    (root / ".gitkeep").write_text("")
    client = _client(root)
    names = {e["name"] for e in client.get("/api/fs/tree").json()["entries"]}
    assert ".gitkeep" not in names
    names_internal = {e["name"] for e in client.get("/api/fs/tree?show_internal=true").json()["entries"]}
    assert ".gitkeep" in names_internal


def test_tree_subdir_and_table_linkage(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    body = client.get("/api/fs/tree?path=sensors").json()
    assert body["path"] == "sensors"
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["temp.raw"]["table_name"] == "temp"
    assert by_name["temp.config.toml"]["table_name"] is None


def test_tree_refuses_path_traversal(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    for bad in ("..", "../..", "/etc", "%2e%2e"):
        r = client.get("/api/fs/tree", params={"path": bad})
        assert r.status_code == 400, bad


def test_tree_refuses_file_path(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/tree", params={"path": "bin.raw"}).status_code == 400


# ---------- read ----------

def test_read_text_file(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.get("/api/fs/read", params={"path": "sensors/temp.raw"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_text"] is True
    assert body["content"] == "payload data\n"
    assert body["size"] == len("payload data\n")


def test_read_binary_returns_hex_rows(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    body = client.get("/api/fs/read", params={"path": "bin.raw"}).json()
    assert body["is_text"] is False
    assert body["rows"][0]["hex"] == "00 01 02 03 FF FE"
    assert body["rows"][0]["ascii"] == "......"
    assert body["has_more"] is False


def test_read_binary_paging(tmp_path):
    root = _project(tmp_path)
    (root / "blob.bin").write_bytes(bytes(range(100, 200)))  # >=0x80: not UTF-8
    client = _client(root)
    first = client.get("/api/fs/read", params={"path": "blob.bin", "limit": 32}).json()
    assert first["has_more"] is True
    assert len(first["rows"]) == 2
    second = client.get("/api/fs/read", params={"path": "blob.bin", "offset": 32, "limit": 32}).json()
    assert second["rows"][0]["offset"] == 32
    assert second["has_more"] is True
    last = client.get("/api/fs/read", params={"path": "blob.bin", "offset": 96, "limit": 32}).json()
    assert last["has_more"] is False
    assert last["rows"][-1]["offset"] == 96


def test_read_truncates_large_text(tmp_path, monkeypatch):
    import payload.web.routes.fs as fs
    monkeypatch.setattr(fs, "MAX_TEXT_CONTENT", 10)
    root = _project(tmp_path)
    (root / "long.txt").write_text("x" * 100)
    client = _client(root)
    body = client.get("/api/fs/read", params={"path": "long.txt"}).json()
    assert body["is_text"] is True
    assert body["truncated"] is True
    assert len(body["content"]) == 10


def test_read_binary_extension_wins_over_content(tmp_path):
    """A known-binary extension (e.g. .bin) always gets the hex view,
    even when its bytes decode as UTF-8 — the text editor would
    re-encode and corrupt it on save. The same content in a .txt stays
    text."""
    root = _project(tmp_path)
    content = b"printable but really binary"
    (root / "fake.bin").write_bytes(content)
    (root / "note.txt").write_bytes(content)
    client = _client(root)
    bin_body = client.get("/api/fs/read", params={"path": "fake.bin"}).json()
    assert bin_body["is_text"] is False
    assert bin_body["rows"][0]["ascii"].startswith("printable but")
    txt_body = client.get("/api/fs/read", params={"path": "note.txt"}).json()
    assert txt_body["is_text"] is True


def test_read_as_hex_forces_hex_view(tmp_path):
    """?as_hex=1 shows even a text file as hex — the 'View as hex'
    escape hatch for inspecting any file at the byte level."""
    root = _project(tmp_path)
    (root / "note.txt").write_text("hello world\n")
    client = _client(root)
    normal = client.get("/api/fs/read", params={"path": "note.txt"}).json()
    assert normal["is_text"] is True
    forced = client.get("/api/fs/read", params={"path": "note.txt", "as_hex": "1"}).json()
    assert forced["is_text"] is False
    assert forced["rows"][0]["hex"].startswith("68 65 6C 6C")  # "hell"


def test_read_hex_reports_can_view_as_text(tmp_path):
    """The hex view tells the client whether the bytes would have decoded
    as text, so a text file shown as hex (via as_hex) can toggle back —
    a real binary extension never can."""
    root = _project(tmp_path)
    (root / "note.txt").write_text("hello\n")
    client = _client(root)
    forced = client.get("/api/fs/read", params={"path": "note.txt", "as_hex": "1"}).json()
    assert forced["is_text"] is False
    assert forced["can_view_as_text"] is True
    bin_body = client.get("/api/fs/read", params={"path": "bin.raw"}).json()
    assert bin_body["is_text"] is False
    assert bin_body["can_view_as_text"] is False


def test_read_hex_reports_end_offset(tmp_path):
    """The hex response includes end_offset (byte after the last shown) so
    the client can display the visible byte range, not just the start."""
    root = _project(tmp_path)
    (root / "blob.bin").write_bytes(bytes(range(0x80, 0x90)))  # 16 bytes
    client = _client(root)
    body = client.get("/api/fs/read", params={"path": "blob.bin"}).json()
    assert body["is_text"] is False
    assert body["offset"] == 0
    assert body["end_offset"] == 16


def test_read_refuses_missing_and_directory(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/read", params={"path": "nope.raw"}).status_code == 400
    assert client.get("/api/fs/read", params={"path": "sensors"}).status_code == 400


# ---------- write / create / rename / copy / delete ----------

def test_write_updates_file(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.put("/api/fs/write", json={"path": "sensors/temp.raw", "content": "new\n"})
    assert r.status_code == 200
    assert (root / "sensors" / "temp.raw").read_text() == "new\n"
    # binary content round-trips as plain text write
    client.put("/api/fs/write", json={"path": "new.txt", "content": "hi"})
    assert (root / "new.txt").read_text() == "hi"


def test_write_refuses_directory_and_traversal(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.put("/api/fs/write", json={"path": "sensors", "content": "x"}).status_code == 400
    assert client.put("/api/fs/write", json={"path": "../evil", "content": "x"}).status_code == 400


def test_create_file_and_dir(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/create", json={"path": "docs", "type": "dir"}).status_code == 200
    assert client.post("/api/fs/create", json={"path": "docs/note.txt", "type": "file"}).status_code == 200
    assert (root / "docs" / "note.txt").exists()
    # nested file creates parents
    assert client.post("/api/fs/create", json={"path": "a/b/c.txt", "type": "file"}).status_code == 200
    assert (root / "a" / "b" / "c.txt").exists()


def test_create_refuses_existing(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/create", json={"path": "bin.raw", "type": "file"}).status_code == 400


def test_rename_and_move(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/rename", json={"path": "bin.raw", "new_path": "renamed.raw"}).status_code == 200
    assert not (root / "bin.raw").exists()
    assert (root / "renamed.raw").exists()
    # move across folders
    assert client.post("/api/fs/rename", json={"path": "renamed.raw", "new_path": "sensors/renamed.raw"}).status_code == 200
    assert (root / "sensors" / "renamed.raw").exists()
    # refuses: existing target, root itself
    assert client.post("/api/fs/rename", json={"path": "sensors/renamed.raw", "new_path": "table-tool.toml"}).status_code == 400
    assert client.post("/api/fs/rename", json={"path": ".", "new_path": "x"}).status_code == 400


def test_copy_file_and_dir(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/copy", json={"path": "bin.raw", "new_path": "bin-copy.raw"}).status_code == 200
    assert (root / "bin-copy.raw").read_bytes() == (root / "bin.raw").read_bytes()
    assert client.post("/api/fs/copy", json={"path": "sensors", "new_path": "sensors-copy"}).status_code == 200
    assert (root / "sensors-copy" / "temp.raw").exists()
    assert client.post("/api/fs/copy", json={"path": "bin.raw", "new_path": "bin.raw"}).status_code == 400


def test_rename_refuses_missing_destination_folder(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/rename", json={"path": "bin.raw", "new_path": "nope/x.raw"}).status_code == 400


def test_strings_finds_embedded_ascii(tmp_path):
    root = _project(tmp_path)
    (root / "fw.bin").write_bytes(b"\x00\x01DATA\xff\x02magic_value\x00\x00")
    client = _client(root)
    body = client.get("/api/fs/strings", params={"path": "fw.bin"}).json()
    assert [(s["offset"], s["text"]) for s in body["strings"]] == [(2, "DATA"), (8, "magic_value")]


def test_strings_trailing_run_at_eof(tmp_path):
    """A file ending in printable text (no terminator byte after the
    last run) — the post-loop flush branch of fs_strings."""
    root = _project(tmp_path)
    (root / "t.bin").write_bytes(b"\xff\x00ending_text")
    client = _client(root)
    body = client.get("/api/fs/strings", params={"path": "t.bin"}).json()
    assert [(s["offset"], s["text"]) for s in body["strings"]] == [(2, "ending_text")]


def test_strings_empty_and_validation(tmp_path):
    root = _project(tmp_path)
    (root / "noise.bin").write_bytes(b"\x00\xff\x01\xfe" * 10)
    client = _client(root)
    assert client.get("/api/fs/strings", params={"path": "noise.bin"}).json()["strings"] == []
    assert client.get("/api/fs/strings").status_code == 400  # missing path
    assert client.get("/api/fs/strings", params={"path": "ghost.bin"}).status_code == 400
    assert client.get("/api/fs/strings", params={"path": "sensors"}).status_code == 400


def test_delete_preview_then_confirm(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    preview = client.post("/api/fs/delete", json={"path": "sensors"}).json()
    assert preview["status"] == "confirmation_required"
    assert preview["is_dir"] is True
    assert preview["entries"] == 3  # temp.raw + temp.config.toml + dir itself
    assert (root / "sensors").exists()
    done = client.post("/api/fs/delete", json={"path": "sensors", "confirm": True}).json()
    assert done["status"] == "deleted"
    assert not (root / "sensors").exists()
    # single file
    client.post("/api/fs/delete", json={"path": "bin.raw", "confirm": True})
    assert not (root / "bin.raw").exists()


def test_delete_refuses_root(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/delete", json={"path": "."}).status_code == 400


# ---------- upload ----------

def test_upload_files(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.post(
        "/api/fs/upload",
        data={"dir": "sensors"},
        files=[("file", ("a.txt", b"aaa", "text/plain")), ("file", ("b.txt", b"bbb", "text/plain"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == ["sensors/a.txt", "sensors/b.txt"]
    assert body["skipped"] == []
    assert (root / "sensors" / "a.txt").read_bytes() == b"aaa"


def test_upload_sanitizes_names_and_skips_existing(tmp_path):
    root = _project(tmp_path)
    (root / "existing.txt").write_text("old")
    client = _client(root)
    r = client.post(
        "/api/fs/upload",
        data={"dir": "."},
        files=[
            ("file", ("../../evil.txt", b"evil", "text/plain")),
            ("file", ("existing.txt", b"new", "text/plain")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert "evil.txt" in body["imported"]  # name sanitized to basename, still inside root
    assert not (root.parent / "evil.txt").exists()
    assert body["skipped"] == [{"name": "existing.txt", "reason": "already exists"}]
    assert (root / "existing.txt").read_text() == "old"
    # overwrite=true replaces it
    r2 = client.post("/api/fs/upload", data={"dir": ".", "overwrite": "true"}, files=[("file", ("existing.txt", b"new", "text/plain"))])
    assert r2.json()["skipped"] == []
    assert (root / "existing.txt").read_text() == "new"


def test_upload_refuses_non_dir_target(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.post("/api/fs/upload", data={"dir": "bin.raw"}, files=[("file", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 400


# ---------- table-name uniqueness guards (the 'everything broke' bug) ----------
# A file whose stem collides with an existing table name breaks discovery
# project-wide (DuplicateTableNameError) and with it the whole table side
# of the webapp — these guards refuse that at the fs boundary instead.

def test_upload_skips_table_name_collision(tmp_path):
    root = _project(tmp_path)  # 'temp' table = sensors/temp.raw
    client = _client(root)
    r = client.post("/api/fs/upload", data={"dir": "."}, files=[("file", ("temp.csv", b"0x0A\n", "text/plain"))])
    body = r.json()
    assert body["imported"] == []
    assert body["skipped"][0]["name"] == "temp.csv"
    assert "table name 'temp'" in body["skipped"][0]["reason"]
    assert not (root / "temp.csv").exists()
    assert not (root / "sensors" / "temp.csv").exists()


def test_upload_to_tables_own_source_is_allowed(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    # overwriting the table's own source (same stem, same path) is fine
    r = client.post(
        "/api/fs/upload", data={"dir": "sensors", "overwrite": "true"},
        files=[("file", ("temp.raw", b"new content\n", "text/plain"))],
    )
    assert r.json()["imported"] == ["sensors/temp.raw"]
    assert (root / "sensors" / "temp.raw").read_text() == "new content\n"


def test_create_refuses_table_name_collision(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/create", json={"path": "temp.csv", "type": "file"}).status_code == 400
    # a non-colliding name is fine
    assert client.post("/api/fs/create", json={"path": "other.csv", "type": "file"}).status_code == 200


def test_rename_refuses_table_name_collision_but_allows_own_source_move(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    # renaming another file to a stem that is a table name -> refused
    assert client.post("/api/fs/rename", json={"path": "bin.raw", "new_path": "temp.csv"}).status_code == 400
    # moving the table's OWN source within the same stem -> allowed
    assert client.post("/api/fs/rename", json={"path": "sensors/temp.raw", "new_path": "temp.raw"}).status_code == 200
    assert (root / "temp.raw").exists()


def test_copy_refuses_table_name_collision(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.post("/api/fs/copy", json={"path": "bin.raw", "new_path": "temp.csv"}).status_code == 400
    assert client.post("/api/fs/copy", json={"path": "bin.raw", "new_path": "bin-copy.raw"}).status_code == 200


# ---------- download ----------

def test_download_serves_file(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.get("/api/fs/download", params={"path": "sensors/temp.raw"})
    assert r.status_code == 200
    assert r.content == b"payload data\n"
    assert "temp.raw" in r.headers["content-disposition"]


def test_download_refuses_directory_and_traversal(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/download", params={"path": "sensors"}).status_code == 400
    assert client.get("/api/fs/download", params={"path": "../secret"}).status_code == 400


# ---------- containment (the critical property) ----------

def test_all_mutating_routes_refuse_escapes(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    cases = [
        ("put", "/api/fs/write", {"path": "../x", "content": "x"}),
        ("post", "/api/fs/create", {"path": "../../x", "type": "file"}),
        ("post", "/api/fs/rename", {"path": "bin.raw", "new_path": "../../x"}),
        ("post", "/api/fs/copy", {"path": "../../x", "new_path": "y"}),
        ("post", "/api/fs/delete", {"path": "../../x"}),
    ]
    for method, url, payload in cases:
        r = getattr(client, method)(url, json=payload)
        assert r.status_code == 400, (method, url)
    # absolute path outside root
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    r = client.get("/api/fs/read", params={"path": str(outside)})
    assert r.status_code == 400


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_symlink_escape_is_refused(tmp_path):
    root = _project(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (root / "link.txt").symlink_to(secret)
    client = _client(root)
    r = client.get("/api/fs/read", params={"path": "link.txt"})
    assert r.status_code == 400  # resolves outside root -> refused


# ---------- table linkage degradation ----------

def test_tree_survives_duplicate_table_names(tmp_path):
    """Duplicate table names break strict discovery — the file browser
    must still work (it's also the recovery tool for a broken project):
    entries appear, and the lenient context still links them to the
    table name (both files share it; the dashboard shows the warning)."""
    root = _project(tmp_path)
    (root / "dup").mkdir()
    (root / "dup" / "temp.raw").write_text("x")  # same stem as sensors/temp.raw
    client = _client(root)
    r = client.get("/api/fs/tree", params={"path": "dup"})
    assert r.status_code == 200
    by_name = {e["name"]: e for e in r.json()["entries"]}
    assert by_name["temp.raw"]["table_name"] == "temp"


def test_tree_survives_malformed_batch_table(tmp_path):
    """A valid config but an invalid [[batch_table]] (discovery raises):
    the file browser keeps working (it's the recovery tool) — this
    exercises the _table_context fallback to an empty context."""
    root = _project(tmp_path)
    cfg = (root / "table-tool.toml").read_text()
    (root / "table-tool.toml").write_text(cfg + '\n[[batch_table]]\nsources = ["ROW*.txt"]\n')  # missing 'name'
    client = _client(root)
    r = client.get("/api/fs/tree")
    assert r.status_code == 200
    assert "bin.raw" in {e["name"] for e in r.json()["entries"]}


def test_tree_marks_batch_members_and_sidecars(tmp_path):
    """Table context in the tree: batch members link to the batch name,
    sidecars link to the table they configure, plain sources are marked
    as tables but not as batch members."""
    root = _project(tmp_path)
    cfg = (root / "table-tool.toml").read_text()
    (root / "table-tool.toml").write_text(cfg + '\n[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n')
    (root / "ROW1.txt").write_text("r1")
    (root / "ROW2.txt").write_text("r2")
    client = _client(root)

    sensors = {e["name"]: e for e in client.get("/api/fs/tree", params={"path": "sensors"}).json()["entries"]}
    assert sensors["temp.config.toml"]["sidecar_table"] == "temp"
    assert sensors["temp.config.toml"]["table_name"] is None
    assert sensors["temp.raw"]["table_name"] == "temp"
    assert sensors["temp.raw"]["is_batch_member"] is False

    top = {e["name"]: e for e in client.get("/api/fs/tree").json()["entries"]}
    assert top["ROW1.txt"]["table_name"] == "rows"
    assert top["ROW1.txt"]["is_batch_member"] is True
    assert top["ROW2.txt"]["is_batch_member"] is True
    assert top["bin.raw"]["table_name"] == "bin"
    assert top["bin.raw"]["is_batch_member"] is False


# ---------- validation branches (kept at 100% coverage) ----------

def test_validation_branches(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    # fs_read
    assert client.get("/api/fs/read").status_code == 400  # missing path
    assert client.get("/api/fs/read", params={"path": "bin.raw", "offset": "abc"}).status_code == 400  # bad offset
    # fs_write
    assert client.put("/api/fs/write", json={"content": "x"}).status_code == 400  # missing path
    assert client.put("/api/fs/write", json={"path": "bin.raw", "content": 123}).status_code == 400  # content not str
    # fs_create
    assert client.post("/api/fs/create", json={"type": "file"}).status_code == 400  # missing path
    assert client.post("/api/fs/create", json={"path": "x", "type": "weird"}).status_code == 400  # bad type
    # fs_rename
    assert client.post("/api/fs/rename", json={"path": "bin.raw"}).status_code == 400  # missing new_path
    assert client.post("/api/fs/rename", json={"path": "ghost.raw", "new_path": "g.raw"}).status_code == 400  # source missing
    # fs_copy
    assert client.post("/api/fs/copy", json={"path": "bin.raw"}).status_code == 400  # missing new_path
    assert client.post("/api/fs/copy", json={"path": "ghost.raw", "new_path": "g.raw"}).status_code == 400  # source missing
    # fs_delete
    assert client.post("/api/fs/delete", json={}).status_code == 400  # missing path
    assert client.post("/api/fs/delete", json={"path": "ghost.raw"}).status_code == 400  # missing
    # fs_upload
    assert client.post("/api/fs/upload", data={"dir": "."}).status_code == 400  # no file field
    # fs_download
    assert client.get("/api/fs/download").status_code == 400  # missing path


def test_upload_empty_filename_is_skipped(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.post("/api/fs/upload", data={"dir": "."}, files=[("file", ("", b"", "text/plain"))])
    assert r.status_code == 200
    assert r.json()["imported"] == []
    assert r.json()["skipped"] == [{"name": "", "reason": "empty filename"}]


# ---------- compare / search / analyze ----------

def test_compare_two_files(tmp_path):
    root = _project(tmp_path)
    (root / "x.bin").write_bytes(b"hello world")
    (root / "y.bin").write_bytes(b"hello Xorld")
    client = _client(root)
    r = client.get("/api/fs/compare", params={"path_a": "x.bin", "path_b": "y.bin"})
    assert r.status_code == 200
    body = r.json()
    assert body["equal"] is False
    assert body["prefix"] == 6
    assert body["runs"] == [{"offset": 6, "length": 1}]
    assert body["a"] == "x.bin"
    assert body["b"] == "y.bin"


def test_compare_validation(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/compare").status_code == 400  # missing params
    assert client.get("/api/fs/compare", params={"path_a": "x.bin", "path_b": "y.bin"}).status_code == 400
    assert client.get("/api/fs/compare", params={"path_a": "x.bin", "path_b": "../evil"}).status_code == 400


def test_search_text_and_hex(tmp_path):
    root = _project(tmp_path)
    (root / "a.raw").write_text("value 0x0A here\n")
    (root / "b.raw").write_text("nothing\n")
    client = _client(root)
    r = client.get("/api/fs/search", params={"q": "0x0A"})
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["path"] == "a.raw"
    assert body["matches"][0]["hex"] == "30 78 30 41"
    r2 = client.get("/api/fs/search", params={"hex": "0A1B"})
    assert r2.status_code == 200
    assert r2.json()["matches"] == []


def test_search_validation(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/search").status_code == 400  # neither q nor hex
    assert client.get("/api/fs/search", params={"q": "x", "hex": "0A"}).status_code == 400  # both
    assert client.get("/api/fs/search", params={"hex": "0A1"}).status_code == 400  # odd digits
    assert client.get("/api/fs/search", params={"q": "x", "path": "bin.raw"}).status_code == 400  # not a dir
    assert client.get("/api/fs/search", params={"q": "x", "limit": "abc"}).status_code == 400


def test_search_in_subdir_returns_project_relative_paths(tmp_path):
    root = _project(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "needle.txt").write_text("needle")
    client = _client(root)
    body = client.get("/api/fs/search", params={"q": "needle", "path": "sub"}).json()
    assert body["matches"][0]["path"] == "sub/needle.txt"


def test_analyze_file(tmp_path):
    root = _project(tmp_path)
    (root / "fw.elf").write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 30)
    client = _client(root)
    r = client.get("/api/fs/analyze", params={"path": "fw.elf"})
    assert r.status_code == 200
    body = r.json()
    assert "ELF executable" in body["magic"]
    assert body["size"] == 37
    assert 0 <= body["entropy"] <= 8
    assert body["freq"]
    assert body["path"] == "fw.elf"


def test_analyze_validation(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    assert client.get("/api/fs/analyze").status_code == 400
    assert client.get("/api/fs/analyze", params={"path": "sensors"}).status_code == 400
    assert client.get("/api/fs/analyze", params={"path": "../evil"}).status_code == 400


def test_compare_path_b_not_a_file(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.get("/api/fs/compare", params={"path_a": "sensors/temp.raw", "path_b": "ghost.bin"})
    assert r.status_code == 400


def test_search_invalid_hex_bytes(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    r = client.get("/api/fs/search", params={"hex": "ZZ"})  # even digits but not hex
    assert r.status_code == 400


def test_list_all_files_skips_internal(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    body = client.get("/api/fs/list").json()
    assert "sensors/temp.raw" in body["files"]
    assert "table-tool.toml" in body["files"]
    assert not any(f.startswith(("build/", "plugins/", ".", ".")) for f in body["files"])


def test_list_subdir_and_validation(tmp_path):
    root = _project(tmp_path)
    client = _client(root)
    body = client.get("/api/fs/list", params={"path": "sensors"}).json()
    assert body["files"] == ["sensors/temp.config.toml", "sensors/temp.raw"]
    assert client.get("/api/fs/list", params={"path": "bin.raw"}).status_code == 400
    assert client.get("/api/fs/list", params={"path": "../evil"}).status_code == 400


def test_list_truncates(monkeypatch, tmp_path):
    import payload.web.routes.fs as fs
    monkeypatch.setattr(fs, "MAX_LIST_FILES", 2)
    root = _project(tmp_path)
    client = _client(root)
    body = client.get("/api/fs/list").json()
    assert len(body["files"]) == 2
    assert body["truncated"] is True
