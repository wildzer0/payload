"""CLI tests for 'pld cluster ...'/'pld tag'/'pld tags' and
'pld build-all --cluster' — same pattern (CliRunner, real raw_text
reader) as test_cli_batch_tables.py."""
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


# --- pld cluster new / list / show ------------------------------------------

def test_cluster_new_creates_entry(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex"])

    assert result.exit_code == 0, result.stdout
    assert 'name = "sensors"' in (proj / "table-tool.toml").read_text()


def test_cluster_new_with_no_overrides(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "new", "sensors"])
    assert result.exit_code == 0, result.stdout


def test_cluster_new_duplicate_name_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])

    result = runner.invoke(app, ["cluster", "new", "sensors"])

    assert result.exit_code != 0


def test_cluster_list_empty(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "list"])
    assert result.exit_code == 0, result.stdout
    assert "No cluster declared" in result.stdout


def test_cluster_list_shows_overrides_and_members(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["cluster", "list"])

    assert result.exit_code == 0, result.stdout
    assert "sensors" in result.stdout
    assert "writer=hex" in result.stdout


def test_cluster_show_prints_overrides_and_members(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["cluster", "show", "sensors"])

    assert result.exit_code == 0, result.stdout
    assert "writer = hex" in result.stdout
    assert "example_table" in result.stdout


def test_cluster_show_with_plugin_override(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text()
        + '\n[[cluster]]\nname = "sensors"\n\n[cluster.plugin.c_source]\ncompiler = "gcc"\n'
    )

    result = runner.invoke(app, ["cluster", "show", "sensors"])

    assert result.exit_code == 0, result.stdout
    assert "c_source" in result.stdout


def test_cluster_show_no_overrides(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "empty"])

    result = runner.invoke(app, ["cluster", "show", "empty"])

    assert result.exit_code == 0, result.stdout
    assert "no overrides" in result.stdout


def test_cluster_show_unknown_name_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "show", "does_not_exist"])
    assert result.exit_code != 0


# --- pld cluster edit --------------------------------------------------------

def test_cluster_edit_sets_field(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex"])

    result = runner.invoke(app, ["cluster", "edit", "sensors", "--output-dir", "build/sensors"])

    assert result.exit_code == 0, result.stdout
    text = (proj / "table-tool.toml").read_text()
    assert "build/sensors" in text
    assert 'writer = "hex"' in text  # untouched


def test_cluster_edit_clears_field(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex", "--output-dir", "build/sensors"])

    result = runner.invoke(app, ["cluster", "edit", "sensors", "--clear-writer"])

    assert result.exit_code == 0, result.stdout
    show = runner.invoke(app, ["cluster", "show", "sensors"])
    assert "writer" not in show.stdout
    assert "build/sensors" in show.stdout


def test_cluster_edit_all_fields(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])

    result = runner.invoke(app, [
        "cluster", "edit", "sensors",
        "--writer", "hex", "--reader", "raw_text",
        "--output-dir", "build/x", "--cache-dir", ".cache/x", "--byte-order", "big",
    ])

    assert result.exit_code == 0, result.stdout
    show = runner.invoke(app, ["cluster", "show", "sensors"])
    assert "hex" in show.stdout and "raw_text" in show.stdout and "big" in show.stdout


def test_cluster_edit_unknown_name_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "edit", "does_not_exist", "--writer", "hex"])
    assert result.exit_code != 0


# --- pld cluster delete -------------------------------------------------------

def test_cluster_delete_removes_entry(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])

    result = runner.invoke(app, ["cluster", "delete", "sensors"])

    assert result.exit_code == 0, result.stdout
    assert 'name = "sensors"' not in (proj / "table-tool.toml").read_text()


def test_cluster_delete_unknown_name_reports_not_found(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "delete", "does_not_exist"])
    assert result.exit_code == 0, result.stdout
    assert "no" in result.stdout.lower()


def test_cluster_delete_with_members_refuses_without_force(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["cluster", "delete", "sensors"])

    assert result.exit_code != 0


def test_cluster_delete_with_force_succeeds(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["cluster", "delete", "sensors", "--force"])

    assert result.exit_code == 0, result.stdout


# --- pld cluster assign / unassign --------------------------------------------

def test_cluster_assign_and_unassign(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])

    result = runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])
    assert result.exit_code == 0, result.stdout
    assert 'cluster = "sensors"' in (proj / "table-tool.toml").read_text()

    result2 = runner.invoke(app, ["cluster", "unassign", "example_table"])
    assert result2.exit_code == 0, result2.stdout
    assert 'cluster = "sensors"' not in (proj / "table-tool.toml").read_text()


def test_cluster_assign_unknown_table_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])
    result = runner.invoke(app, ["cluster", "assign", "does_not_exist", "sensors"])
    assert result.exit_code == 4


def test_cluster_assign_unknown_cluster_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "assign", "example_table", "does_not_exist"])
    assert result.exit_code != 0


def test_cluster_unassign_unknown_table_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cluster", "unassign", "does_not_exist"])
    assert result.exit_code == 4


# --- pld tag / pld tags -------------------------------------------------------

def test_tag_shows_no_tags_by_default(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["tag", "example_table"])
    assert result.exit_code == 0, result.stdout
    assert "no tags" in result.stdout


def test_tag_add_and_show(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["tag", "example_table", "--add", "prod", "--add", "beta"])

    result = runner.invoke(app, ["tag", "example_table"])

    assert result.exit_code == 0, result.stdout
    assert "prod" in result.stdout and "beta" in result.stdout


def test_tag_add_does_not_duplicate(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["tag", "example_table", "--add", "prod"])
    runner.invoke(app, ["tag", "example_table", "--add", "prod"])

    result = runner.invoke(app, ["tag", "example_table"])

    assert result.stdout.count("prod") == 1


def test_tag_remove(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["tag", "example_table", "--add", "prod", "--add", "beta"])

    result = runner.invoke(app, ["tag", "example_table", "--remove", "beta"])

    assert result.exit_code == 0, result.stdout
    assert "prod" in result.stdout
    assert "beta" not in result.stdout


def test_tag_add_and_remove_together(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["tag", "example_table", "--add", "prod"])

    result = runner.invoke(app, ["tag", "example_table", "--add", "beta", "--remove", "prod"])

    assert result.exit_code == 0, result.stdout
    assert "beta" in result.stdout
    assert "prod" not in result.stdout


def test_tag_unknown_table_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["tag", "does_not_exist", "--add", "x"])
    assert result.exit_code == 4


def test_tags_project_wide_empty(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["tags"])
    assert result.exit_code == 0, result.stdout
    assert "No tag in use" in result.stdout


def test_tags_project_wide_lists_counts(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["tag", "example_table", "--add", "prod"])

    result = runner.invoke(app, ["tags"])

    assert result.exit_code == 0, result.stdout
    assert "prod" in result.stdout
    assert "1" in result.stdout


# --- pld build-all --cluster --------------------------------------------------

def test_build_all_cluster_restricts_to_members(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "other.raw").write_text("0x01\n")
    runner.invoke(app, ["cluster", "new", "sensors"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["build-all", "--cluster", "sensors"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "build" / "example_table.bin").exists()
    assert not (proj / "build" / "other.bin").exists()


def test_build_all_cluster_unknown_name_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["build-all", "--cluster", "does_not_exist"])
    assert result.exit_code != 0


def test_build_all_cluster_combines_with_filter(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "other.raw").write_text("0x01\n")
    runner.invoke(app, ["cluster", "new", "sensors"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])
    runner.invoke(app, ["cluster", "assign", "other", "sensors"])

    result = runner.invoke(app, ["build-all", "--cluster", "sensors", "--filter", "example_table.raw"])

    assert result.exit_code == 0, result.stdout
    assert (proj / "build" / "example_table.bin").exists()
    assert not (proj / "build" / "other.bin").exists()


# --- pld report with cluster/tags columns -------------------------------------

def test_report_shows_cluster_and_tags_columns(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])
    runner.invoke(app, ["tag", "example_table", "--add", "prod"])

    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0, result.stdout
    assert "sensors" in result.stdout
    assert "prod" in result.stdout


def test_report_hides_cluster_tags_columns_when_unused(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.stdout
    assert "Cluster" not in result.stdout


# --- pld config show reflects cluster provenance ------------------------------

def test_config_show_cluster_provenance(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["cluster", "new", "sensors", "--writer", "hex"])
    runner.invoke(app, ["cluster", "assign", "example_table", "sensors"])

    result = runner.invoke(app, ["config", "show", "example_table"])

    assert result.exit_code == 0, result.stdout
    assert "cluster" in result.stdout.lower()


# --- pld watch on_change picks up a batch table's cluster ----------------------

def test_watch_on_change_batch_uses_cluster_override(tmp_path, monkeypatch, capsys):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "ROW1.txt").write_text("0x01\n")
    (proj / "ROW2.txt").write_text("0x02\n")
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text()
        + '\n[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
        + '\n[[cluster]]\nname = "sensors"\n\n[cluster.defaults]\nwriter = "bin"\n'
        + '\n[[table_meta]]\nname = "rows"\ncluster = "sensors"\n'
    )
    captured = {}

    def fake_watch_loop(root, out, on_change, cache_dir=None):
        captured["on_change"] = on_change

    monkeypatch.setattr("payload.cli.watch_loop", fake_watch_loop)
    result = runner.invoke(app, ["watch", "."])
    assert result.exit_code == 0, result.stdout

    captured["on_change"](proj / "ROW1.txt")
    out = capsys.readouterr().out
    assert "member of 'rows'" in out
    assert (proj / "build" / "rows.bin").exists()
