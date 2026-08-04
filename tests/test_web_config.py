from pathlib import Path

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


def test_config_global(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/config")

    assert r.status_code == 200
    keys = {f["key"] for f in r.json()["fields"]}
    assert "defaults.writer" in keys


def test_config_for_table_with_sidecar(tmp_path):
    root = _init_project(tmp_path)
    (root / "example_table.config.toml").write_text('[plugin.custom]\nkey = "value"\n')
    client = _client(root)

    r = client.get("/api/config", params={"table": "example_table"})

    assert r.status_code == 200
    plugin_fields = [f for f in r.json()["fields"] if f["key"] == "plugin.custom.key"]
    assert plugin_fields and plugin_fields[0]["origin"].startswith("sidecar")


def test_config_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/config", params={"table": "does_not_exist"})

    assert r.status_code == 404


def test_pipeline_implicit(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/pipeline/example_table")

    assert r.status_code == 200
    body = r.json()
    assert body["stages"][0]["kind"] == "reader"
    assert body["stages"][1]["kind"] == "writer"
    assert body["outputs"][0].endswith(".bin")


def test_pipeline_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/pipeline/does_not_exist")

    assert r.status_code == 404


def test_config_put_writes_global_config(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    current = client.get("/api/config").json()
    defaults = {f["key"].split(".", 1)[1]: f["value"] for f in current["fields"] if f["key"].startswith("defaults.")}
    defaults["writer"] = "hex"

    r = client.put("/api/config", json={"defaults": defaults})

    assert r.status_code == 200
    fields = {f["key"]: f["value"] for f in r.json()["fields"]}
    assert fields["defaults.writer"] == "hex"
    assert (root / "table-tool.toml").exists()


def test_config_put_rejects_missing_defaults(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/config", json={})

    assert r.status_code == 400


def test_config_put_rejects_unknown_field(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/config", json={"defaults": {"campo_inventato": 1}})

    assert r.status_code == 400


def test_config_schema_present_in_response(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/config")

    schema = r.json()["schema"]
    assert {f["key"] for f in schema["defaults"]} == {"writer", "reader", "output_dir", "cache_dir", "byte_order"}


def test_sidecar_get_empty_when_missing(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/sidecar/example_table")

    assert r.status_code == 200
    assert r.json() == {}


def test_sidecar_unknown_table_404(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.get("/api/sidecar/does_not_exist")

    assert r.status_code == 404


def test_sidecar_put_then_get_round_trips(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/sidecar/example_table", json={"defaults": {"writer": "hex"}})

    assert r.status_code == 200
    assert r.json() == {"defaults": {"writer": "hex"}}
    assert (root / "example_table.config.toml").exists()

    r2 = client.get("/api/sidecar/example_table")
    assert r2.json() == {"defaults": {"writer": "hex"}}


def test_sidecar_put_rejects_non_dict_defaults(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/sidecar/example_table", json={"defaults": "not a table"})

    assert r.status_code == 400


def test_sidecar_put_rejects_invalid_field(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/sidecar/example_table", json={"defaults": {"byte_order": "middle"}})

    assert r.status_code == 400


def test_sidecar_delete_idempotent(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    client.put("/api/sidecar/example_table", json={"defaults": {"writer": "hex"}})

    r1 = client.delete("/api/sidecar/example_table")
    r2 = client.delete("/api/sidecar/example_table")

    assert r1.json()["status"] == "deleted"
    assert r2.json()["status"] == "not_found"
    assert not (root / "example_table.config.toml").exists()


def test_pipeline_put_writes_explicit_stages(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]

    r = client.put("/api/pipeline/example_table", json={"stages": stages})

    assert r.status_code == 200
    body = r.json()
    assert body["explicit"] is True
    assert [s["kind"] for s in body["stages"]] == ["reader", "writer"]
    assert body["outputs"][0].endswith(".hex")

    r2 = client.get("/api/pipeline/example_table")
    assert r2.json()["explicit"] is True


def test_pipeline_put_with_terminal_exec_stage_round_trips(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [
        {"type": "reader", "name": "raw_text"},
        {"type": "writer", "name": "bin"},
        {"type": "exec", "command": "cp {input} {output}", "output_extension": ".copy"},
    ]

    r = client.put("/api/pipeline/example_table", json={"stages": stages})

    assert r.status_code == 200
    exec_stage = r.json()["stages"][-1]
    assert exec_stage["kind"] == "exec"
    assert exec_stage["command"] == "cp {input} {output}"

    r2 = client.get("/api/pipeline/example_table")
    assert r2.json()["stages"][-1]["command"] == "cp {input} {output}"


def test_pipeline_put_rejects_non_list_stages(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/pipeline/example_table", json={"stages": "not a list"})

    assert r.status_code == 400


def test_pipeline_put_rejects_invalid_alternation(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/pipeline/example_table", json={"stages": [{"type": "writer", "name": "bin"}]})

    assert r.status_code == 400
    assert "stage_index" in r.json()


def test_pipeline_put_rejects_unknown_reader(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [{"type": "reader", "name": "does_not_exist"}, {"type": "writer", "name": "bin"}]

    r = client.put("/api/pipeline/example_table", json={"stages": stages})

    assert r.status_code == 400


def test_pipeline_delete_reverts_to_implicit(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [{"type": "reader", "name": "raw_text"}, {"type": "writer", "name": "hex"}]
    client.put("/api/pipeline/example_table", json={"stages": stages})

    r = client.delete("/api/pipeline/example_table")

    assert r.status_code == 200
    assert r.json()["explicit"] is False
    assert not (root / "example_table.config.toml").exists()


def test_pipeline_checkpoint_state_transitions(tmp_path):
    root = _init_project(tmp_path)
    (root / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }, '
        '{ type = "exec", command = "cp {input} {output}", output_extension = ".copy" }'
        ']\n'
    )
    client = _client(root)

    before = client.get("/api/pipeline/example_table").json()
    writer_stage = next(s for s in before["stages"] if s["kind"] == "writer")
    assert writer_stage["checkpoint"] == "none"

    client.post("/api/build", json={"source": "example_table.raw"})

    after = client.get("/api/pipeline/example_table").json()
    writer_stage_after = next(s for s in after["stages"] if s["kind"] == "writer")
    assert writer_stage_after["checkpoint"] == "valid"


def test_pipeline_layout_round_trips(tmp_path):
    """The canvas position (x/y) of each stage survives a save + reopen."""
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [
        {"type": "reader", "name": "raw_text", "x": 120, "y": 40},
        {"type": "writer", "name": "hex", "x": 480, "y": 40},
    ]

    r = client.put("/api/pipeline/example_table", json={"stages": stages})
    assert r.status_code == 200
    body = r.json()
    assert [(s["x"], s["y"]) for s in body["stages"]] == [(120, 40), (480, 40)]

    # a fresh GET (reopen) restores the same positions
    r2 = client.get("/api/pipeline/example_table")
    assert r2.status_code == 200
    assert [(s["x"], s["y"]) for s in r2.json()["stages"]] == [(120, 40), (480, 40)]

    # the position is also persisted in the sidecar
    sidecar = (root / "example_table.config.toml").read_text()
    assert "x = 120" in sidecar and "y = 40" in sidecar


def test_pipeline_layout_bad_coordinate_rejected(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)
    stages = [{"type": "reader", "name": "raw_text", "x": "not-a-number"}]
    r = client.put("/api/pipeline/example_table", json={"stages": stages})
    assert r.status_code == 400


def test_byte_order_change_is_committable(tmp_path):
    """A byte_order change is a real (committable) change even when the
    output bytes come out identical (e.g. a reader with no multi-byte
    fields): the snapshot records the effective byte_order."""
    root = _init_project(tmp_path)
    client = _client(root)
    client.post("/api/build", json={"source": str(root / "example_table.raw")})
    client.post("/api/commit", json={"message": "v1", "only": ["example_table"]})
    assert client.get("/api/status").json()["tables"][0]["state"] == "clean"

    # change byte_order only — the output is byte-identical (raw_text
    # has no multi-byte fields) but the state must become dirty
    client.put("/api/sidecar/example_table", json={"defaults": {"byte_order": "big"}})
    assert client.get("/api/status").json()["tables"][0]["state"] == "dirty"

    r = client.post("/api/commit", json={"message": "v2", "only": ["example_table"]})
    assert r.status_code == 200
    assert r.json()["committed"][0]["snapshot_id"] == 2
    # and it's clean again (byte_order is now part of the committed state)
    assert client.get("/api/status").json()["tables"][0]["state"] == "clean"


def test_config_put_plugin_options(tmp_path):
    root = _init_project(tmp_path)
    client = _client(root)

    r = client.put("/api/config", json={"plugin": {"c_source": {"compiler": "gcc"}}})
    assert r.status_code == 200
    assert r.json()["plugin"]["c_source"]["compiler"] == "gcc"
    assert 'compiler = "gcc"' in (root / "table-tool.toml").read_text()

    # defaults alone still works; invalid plugin types are rejected
    r = client.put("/api/config", json={"defaults": {"writer": "hex"}})
    assert r.status_code == 200
    r = client.put("/api/config", json={"defaults": "nope"})
    assert r.status_code == 400
    r = client.put("/api/config", json={"plugin": ["not", "a", "dict"]})
    assert r.status_code == 400
