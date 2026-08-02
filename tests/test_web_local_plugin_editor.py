from pathlib import Path

from starlette.testclient import TestClient
from typer.testing import CliRunner

from payload.cli import app as cli_app
from payload.web.app import create_app

runner = CliRunner()

READER_SOURCE = '''"""Sample reader."""
from pathlib import Path
from payload.core.ir import TableIR


class SampleReader:
    name = "sample_reader"
    extensions = [".sample"]
    api_version = "1.0"

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        return TableIR(name=path.stem, data=b"\\x01\\x02", source_path=path, source_format=self.name)


READER = SampleReader
'''

WRITER_SOURCE = '''"""Sample writer."""
from payload.core.ir import TableIR


class SampleWriter:
    name = "sample_writer"
    extension = ".sample"
    api_version = "1.0"

    def emit(self, ir: TableIR, out_path, config: dict):
        out_path.write_bytes(ir.data)
        return out_path


WRITER = SampleWriter
'''

DOCTOR_CHECK_SOURCE = '''"""Sample check."""
from payload.core.plugin_base import CheckResult, CheckStatus


class SampleCheck:
    name = "sample_check"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        return CheckResult(name=self.name, status=CheckStatus.OK, message="ok")
'''
DOCTOR_CHECK_SOURCE += "\nDOCTOR_CHECK = SampleCheck\n"

BROKEN_SYNTAX_SOURCE = "def broken(:\n    pass\n"

MISSING_DEPS_SOURCE = '''
REQUIRES = ["this_package_really_does_not_exist_12345"]

class X:
    name = "x"
    extensions = [".x"]
    api_version = "1.0"
    def sniff(self, p): return False
    def parse(self, p, c): raise NotImplementedError

READER = X
'''


def _init_project(tmp_path: Path, name: str = "proj") -> Path:
    result = runner.invoke(cli_app, ["init", str(tmp_path / name)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root))


def _write_local_plugin(root: Path, filename: str, source: str) -> Path:
    d = root / "plugins"
    d.mkdir(exist_ok=True)
    path = d / filename
    path.write_text(source)
    return path


# --- list / get / put ---


def test_list_empty_when_no_local_plugins_dir(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/local-plugins")

    assert r.status_code == 200
    assert r.json() == {"files": []}


def test_list_reports_kinds(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    client = _client(root)

    r = client.get("/api/local-plugins")

    files = r.json()["files"]
    assert files[0]["filename"] == "reader.py"
    assert files[0]["kinds"] == ["reader"]


def test_list_still_shows_broken_file_with_empty_kinds(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "broken.py", BROKEN_SYNTAX_SOURCE)
    client = _client(root)

    r = client.get("/api/local-plugins")

    files = r.json()["files"]
    assert files[0]["filename"] == "broken.py"
    assert files[0]["kinds"] == []


def test_list_reports_empty_stub_methods_for_implemented_plugin(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    client = _client(root)

    r = client.get("/api/local-plugins")

    assert r.json()["files"][0]["stub_methods"] == []


def test_list_flags_scaffold_stub_methods(tmp_path):
    """Regression: a file just created by 'pld plugin new-local'
    (scaffold not yet implemented) must be flaggable WITHOUT having to
    run it — see core/local_plugins.py, find_stub_methods."""
    root = _init_project(tmp_path)
    from payload.plugin_scaffold import scaffold_local_plugin

    scaffold_local_plugin("scaffolded_reader", "reader", root / "plugins")
    client = _client(root)

    r = client.get("/api/local-plugins")

    files = r.json()["files"]
    assert files[0]["filename"] == "scaffolded_reader.py"
    assert files[0]["stub_methods"] == ["parse"]


def test_list_excludes_underscore_prefixed(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    _write_local_plugin(root, "_helper.py", "x = 1\n")
    client = _client(root)

    r = client.get("/api/local-plugins")

    assert [f["filename"] for f in r.json()["files"]] == ["reader.py"]


def test_get_returns_content(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    client = _client(root)

    r = client.get("/api/local-plugins/reader.py")

    assert r.status_code == 200
    assert r.json()["content"] == READER_SOURCE


def test_get_unknown_file_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/local-plugins/does_not_exist.py")

    assert r.status_code == 404


def test_get_rejects_path_traversal(tmp_path):
    """Starlette doesn't even match the route for a segment with '/'
    in it (the default converter for {filename} excludes it) — 404,
    not 400: the traversal is impossible either way, just at a
    different level than the one _safe_filename() validates."""
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/local-plugins/..%2F..%2Fetc%2Fpasswd.py")

    assert r.status_code == 404


def test_get_rejects_dotfile(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/local-plugins/.hidden.py")

    assert r.status_code == 400


def test_put_creates_and_overwrites(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/local-plugins/reader.py", json={"content": READER_SOURCE})

    assert r.status_code == 200
    assert (root / "plugins" / "reader.py").read_text() == READER_SOURCE

    r2 = client.put("/api/local-plugins/reader.py", json={"content": "# updated\n" + READER_SOURCE})
    assert r2.status_code == 200
    assert (root / "plugins" / "reader.py").read_text().startswith("# updated")


def test_put_missing_content_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/local-plugins/reader.py", json={})

    assert r.status_code == 400


def test_put_rejects_non_py_filename(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/local-plugins/reader.txt", json={"content": "x"})

    assert r.status_code == 400


# --- delete ---


def test_delete_removes_file(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    client = _client(root)

    r = client.delete("/api/local-plugins/reader.py")

    assert r.status_code == 200
    assert r.json() == {"filename": "reader.py", "status": "deleted"}
    assert not (root / "plugins" / "reader.py").exists()


def test_delete_is_idempotent(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.delete("/api/local-plugins/does_not_exist.py")

    assert r.status_code == 200
    assert r.json() == {"filename": "does_not_exist.py", "status": "not_found"}


def test_delete_rejects_dotfile(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.delete("/api/local-plugins/.hidden.py")

    assert r.status_code == 400


# --- syntax-check ---


def test_syntax_check_valid(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/local-plugins/syntax-check", json={"content": READER_SOURCE})

    assert r.status_code == 200
    assert r.json() == {"valid": True}


def test_syntax_check_invalid_reports_line(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/local-plugins/syntax-check", json={"content": BROKEN_SYNTAX_SOURCE})

    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["line"] == 1


def test_syntax_check_empty_content_is_valid(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/local-plugins/syntax-check", json={})

    assert r.json() == {"valid": True}


# --- test (conformance) ---


def test_run_test_reader_structure_only(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    client = _client(root)

    r = client.post("/api/local-plugins/reader.py/test", json={})

    assert r.status_code == 200
    body = r.json()
    assert body["loadable"] is True
    result = body["results"][0]
    assert result["kind"] == "reader"
    assert result["conforms"] is True
    assert result["skipped_behavior_check"] is True


def test_run_test_reader_with_sample_resolves_relative_to_root(tmp_path, monkeypatch):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "reader.py", READER_SOURCE)
    (root / "sample.sample").write_text("something")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    client = _client(root)

    r = client.post("/api/local-plugins/reader.py/test", json={"sample": "sample.sample"})

    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["skipped_behavior_check"] is False
    assert result["conforms"] is True


def test_run_test_writer(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "writer.py", WRITER_SOURCE)
    client = _client(root)

    r = client.post("/api/local-plugins/writer.py/test", json={})

    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["kind"] == "writer"
    assert result["conforms"] is True
    assert result["skipped_behavior_check"] is False


def test_run_test_doctor_check(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "check.py", DOCTOR_CHECK_SOURCE)
    client = _client(root)

    r = client.post("/api/local-plugins/check.py/test", json={})

    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["kind"] == "doctor_check"
    assert result["conforms"] is True


def test_run_test_syntax_error_reports_not_loadable(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "broken.py", BROKEN_SYNTAX_SOURCE)
    client = _client(root)

    r = client.post("/api/local-plugins/broken.py/test", json={})

    assert r.status_code == 200
    body = r.json()
    assert body["loadable"] is False
    assert "error" in body


def test_run_test_no_plugin_classes_declared(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "empty.py", "x = 1\n")
    client = _client(root)

    r = client.post("/api/local-plugins/empty.py/test", json={})

    assert r.status_code == 200
    assert r.json()["loadable"] is False


def test_run_test_missing_requires_400(tmp_path):
    root = _init_project(tmp_path)
    _write_local_plugin(root, "needsdeps.py", MISSING_DEPS_SOURCE)
    client = _client(root)

    r = client.post("/api/local-plugins/needsdeps.py/test", json={})

    assert r.status_code == 400
    assert "this_package_really_does_not_exist_12345" in str(r.json())


def test_run_test_unknown_file_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/local-plugins/does_not_exist.py/test", json={})

    assert r.status_code == 404


def test_run_test_doctor_check_missing_attributes(tmp_path):
    source = '''
class Incomplete:
    pass

DOCTOR_CHECK = Incomplete
'''
    root = _init_project(tmp_path)
    _write_local_plugin(root, "incomplete.py", source)
    client = _client(root)

    r = client.post("/api/local-plugins/incomplete.py/test", json={})

    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["conforms"] is False
    checks = {i["detail"] for i in result["issues"]}
    assert any("name" in d for d in checks)
    assert any("run" in d for d in checks)


def test_run_test_instantiation_failure_reported_per_class(tmp_path):
    root = _init_project(tmp_path)
    source = '''
class BadReader:
    def __init__(self):
        raise RuntimeError("boom")

READER = BadReader
'''
    _write_local_plugin(root, "bad.py", source)
    client = _client(root)

    r = client.post("/api/local-plugins/bad.py/test", json={})

    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["loadable"] is False
    assert "boom" in result["error"]
