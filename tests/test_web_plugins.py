from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from typer.testing import CliRunner

from payload.cli import app as cli_app
from payload.web.app import create_app

runner = CliRunner()


def _init_project(tmp_path: Path, name: str = "proj") -> Path:
    result = runner.invoke(cli_app, ["init", str(tmp_path / name)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root))


def test_plugins_list(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/plugins")

    assert r.status_code == 200
    names = {p["name"] for p in r.json()["plugins"]}
    assert {"raw_text", "csv", "bin", "hex", "header"}.issubset(names)


def test_plugins_list_marks_payload_own_plugins_as_builtin(tmp_path):
    """raw_text/bin/... ship with payload itself — the webapp puts
    them below plugins the user wrote, see 'builtin' in the response
    and _pluginColumnHtml() in app.js."""
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/plugins")

    by_name = {p["name"]: p for p in r.json()["plugins"]}
    assert by_name["raw_text"]["builtin"] is True
    assert by_name["bin"]["builtin"] is True


def test_plugins_list_marks_local_plugin_as_not_builtin(tmp_path):
    root = _init_project(tmp_path)
    result = runner.invoke(
        cli_app, ["plugin", "new-local", "my_reader", "--kind", "reader", "--dest", str(root / "local_plugins")],
    )
    assert result.exit_code == 0, result.stdout
    client = _client(root)

    r = client.get("/api/plugins")

    by_name = {p["name"]: p for p in r.json()["plugins"]}
    assert by_name["my_reader"]["builtin"] is False


def test_plugin_info_reader(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/plugin/raw_text")

    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "reader"
    assert body["default_writer"] == "bin"
    assert body["builtin"] is True


def test_plugin_info_docstring_preserves_example_indentation(tmp_path):
    """inspect.getdoc() instead of the raw __doc__: PEP 257 dedent on
    the prose lines, but the RELATIVE indentation of the example block
    inside the docstring stays intact — that's what the frontend uses
    to distinguish prose (paragraphs) from example (preformatted
    block)."""
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/plugin/raw_text")

    assert r.status_code == 200
    doc = r.json()["docstring"]
    assert "\n    # whole-line comment, ignored" in doc
    assert doc.startswith("Minimal text format")


def test_plugin_info_unknown_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/plugin/does_not_exist")

    assert r.status_code == 404


def test_plugin_validate_reader_structure_only(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/validate", json={"name": "raw_text"})

    assert r.status_code == 200
    body = r.json()
    assert body["conforms"] is True
    assert body["skipped_behavior_check"] is True


def test_plugin_validate_reader_with_sample(tmp_path):
    root = _init_project(tmp_path)
    sample = root / "sample.raw"
    sample.write_text("0x0A\n")
    client = _client(root)

    r = client.post("/api/plugin/validate", json={"name": "raw_text", "sample": str(sample)})

    assert r.status_code == 200
    assert r.json()["conforms"] is True


def test_plugin_validate_reader_sample_resolves_relative_to_root_not_cwd(tmp_path, monkeypatch):
    """Regression: a relative 'sample' path must be resolved against
    the served project's root, not the server process's cwd (same bug
    fixed in core/doctor.py — 'pld serve' launched from a folder other
    than the project is a legitimate case)."""
    root = _init_project(tmp_path)
    (root / "sample.raw").write_text("0x0A\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    client = _client(root)

    r = client.post("/api/plugin/validate", json={"name": "raw_text", "sample": "sample.raw"})

    assert r.status_code == 200
    assert r.json()["conforms"] is True


def test_plugin_validate_writer(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/validate", json={"name": "bin"})

    assert r.status_code == 200
    assert r.json()["conforms"] is True


def test_plugin_validate_unknown_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/validate", json={"name": "does_not_exist"})

    assert r.status_code == 404


def test_plugin_validate_missing_name_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/validate", json={})

    assert r.status_code == 400


def test_plugin_install_deps_missing_file_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/install-deps", json={})

    assert r.status_code == 400


def test_plugin_install_deps_noop_without_requires(tmp_path):
    root = _init_project(tmp_path)
    f = root / "p.py"
    f.write_text("class X:\n    pass\n")
    client = _client(root)

    r = client.post("/api/plugin/install-deps", json={"file": str(f)})

    assert r.status_code == 200
    assert r.json()["status"] == "noop"
    assert "REQUIRES" in r.json()["reason"]


def test_plugin_install_deps_pip_failure(tmp_path):
    root = _init_project(tmp_path)
    f = root / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')
    client = _client(root)

    with patch("payload.web.routes.plugins.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "pip error"
        r = client.post("/api/plugin/install-deps", json={"file": str(f), "confirm": True})

    assert r.status_code == 422  # BuildError (ToolchainExecutionError) -> 422


def test_plugin_install_deps_noop_when_satisfied(tmp_path):
    root = _init_project(tmp_path)
    f = root / "p.py"
    f.write_text('REQUIRES = ["json", "os"]\n')
    client = _client(root)

    r = client.post("/api/plugin/install-deps", json={"file": str(f)})

    assert r.status_code == 200
    assert r.json()["status"] == "noop"


def test_plugin_install_deps_requires_confirmation(tmp_path):
    root = _init_project(tmp_path)
    f = root / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')
    client = _client(root)

    r = client.post("/api/plugin/install-deps", json={"file": str(f)})

    assert r.status_code == 200
    assert r.json()["status"] == "confirmation_required"


def test_plugin_install_deps_confirmed_calls_pip(tmp_path):
    root = _init_project(tmp_path)
    f = root / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')
    client = _client(root)

    with patch("payload.web.routes.plugins.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        r = client.post("/api/plugin/install-deps", json={"file": str(f), "confirm": True})

    assert r.status_code == 200
    assert r.json()["status"] == "installed"
    mock_run.assert_called_once()


def test_plugin_new_local_creates_file(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new-local", json={"name": "my_format", "kind": "reader"})

    assert r.status_code == 200
    created = Path(r.json()["created"])
    assert created.exists()
    assert "READER = MyFormatReader" in created.read_text()


def test_plugin_new_local_bad_kind_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new-local", json={"name": "x", "kind": "boh"})

    assert r.status_code == 400


def test_plugin_new_local_missing_params_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new-local", json={"name": "x"})

    assert r.status_code == 400


def test_plugin_new_local_existing_file_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/plugin/new-local", json={"name": "dup", "kind": "reader"})

    r = client.post("/api/plugin/new-local", json={"name": "dup", "kind": "reader"})

    assert r.status_code == 400


def test_plugin_new_creates_package(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new", json={"name": "payload-writer-testx", "kind": "writer"})

    assert r.status_code == 200
    created = Path(r.json()["created"])
    assert (created / "pyproject.toml").exists()


def test_plugin_new_missing_params_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new", json={"name": "x"})

    assert r.status_code == 400


def test_plugin_new_bad_kind_400(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.post("/api/plugin/new", json={"name": "payload-writer-boh", "kind": "boh"})

    assert r.status_code == 400
