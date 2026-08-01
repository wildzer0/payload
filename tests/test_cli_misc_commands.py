"""
CLI tests for the remaining commands: view, build-all, watch (on_change),
pipeline show (remaining branches), golden (full update/check/diff),
plugin (info/install-deps/validate/new-local), clean, init (remaining
branches), command-line entry point."""
import subprocess
import sys
from unittest.mock import patch

from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def _init_project(tmp_path, monkeypatch, name="proj"):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", name])
    proj = tmp_path / name
    monkeypatch.chdir(proj)
    return proj


# --- view ----------------------------------------------------------------

def test_view_shows_bytes_and_comments(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "t.raw").write_text("0x0A, 0x1B  # threshold\n")

    result = runner.invoke(app, ["view", "t.raw"])

    assert result.exit_code == 0
    assert "0A" in result.stdout
    assert "threshold" in result.stdout


# --- build: check-golden ----------------------------------------------------

def test_build_check_golden_stale_when_source_changed_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["build", "example_table.raw", "--to", "bin", "--force", "--check-golden"])

    assert result.exit_code == 3


def test_build_check_golden_mismatch_on_tampered_output_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "build" / "example_table.bin").write_bytes(b"tampered by hand")

    # source unchanged -> cache hit, the build doesn't rewrite the tampered output
    result = runner.invoke(app, ["build", "example_table.raw", "--to", "bin", "--check-golden"])

    assert result.exit_code == 3


# --- build-all -------------------------------------------------------------

def test_build_all_success(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "second.raw").write_text("0x02\n")

    result = runner.invoke(app, ["build-all"])

    assert result.exit_code == 0
    assert "2 tables processed" in result.stdout
    assert (proj / "build" / "example_table.bin").exists()
    assert (proj / "build" / "second.bin").exists()


def test_build_all_duplicate_names_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "sub").mkdir()
    (proj / "sub" / "example_table.raw").write_text("0x01\n")

    result = runner.invoke(app, ["build-all"])

    assert result.exit_code != 0


def test_build_all_golden_mismatch_reported_not_fatal(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "build" / "example_table.bin").write_bytes(b"tampered by hand")

    # source unchanged -> cache hit, build-all doesn't rewrite the tampered output
    result = runner.invoke(app, ["build-all", "--check-golden"])

    assert result.exit_code == 0
    assert "golden mismatch" in result.stdout


def test_build_all_real_failure_raises_batch_error(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "broken.raw").write_text("0xZZ\n")

    result = runner.invoke(app, ["build-all"])

    assert result.exit_code == 1


def test_build_all_shows_random_tip_when_lucky(tmp_path, monkeypatch):
    """The tip at the bottom of the page is shown only
    probabilistically (30%) — we force it here instead of relying on
    chance, otherwise the line would be covered or not depending on
    the run's luck."""
    _init_project(tmp_path, monkeypatch)
    with patch("payload.cli.random.random", return_value=0.0):
        result = runner.invoke(app, ["build-all"])
    assert result.exit_code == 0
    assert "💡" in result.stdout


def test_build_all_multiple_jobs(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "second.raw").write_text("0x02\n")

    result = runner.invoke(app, ["build-all", "--jobs", "2"])

    assert result.exit_code == 0


# --- watch: on_change and a partially failed initial build -----------------

def test_watch_initial_build_partial_failure_reported(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "broken.raw").write_text("0xZZ\n")
    monkeypatch.setattr("payload.cli.watch_loop", lambda *a, **k: None)

    result = runner.invoke(app, ["watch", "."])

    assert result.exit_code == 0
    assert "tables failed" in result.stdout


def test_watch_on_change_rebuilds_and_prints(tmp_path, monkeypatch, capsys):
    proj = _init_project(tmp_path, monkeypatch)
    captured = {}

    def fake_watch_loop(root, known_ext, out, on_change):
        captured["on_change"] = on_change

    monkeypatch.setattr("payload.cli.watch_loop", fake_watch_loop)
    result = runner.invoke(app, ["watch", "."])
    assert result.exit_code == 0

    captured["on_change"](proj / "example_table.raw")
    out = capsys.readouterr().out
    assert "example_table.raw" in out


def test_watch_on_change_rebuilds_whole_batch_table(tmp_path, monkeypatch, capsys):
    """A file that's part of a [[batch_table]] triggers a rebuild of
    the WHOLE batch table (not a standalone build of just that file,
    which would produce a wrong/duplicate output) — see
    src/payload/docs/BATCH.md."""
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "ROW1.txt").write_text("0x01\n")
    (proj / "ROW2.txt").write_text("0x02\n")
    (proj / "table-tool.toml").write_text(
        (proj / "table-tool.toml").read_text()
        + '\n[[batch_table]]\nname = "rows"\nsources = ["ROW*.txt"]\n'
    )
    captured = {}

    def fake_watch_loop(root, known_ext, out, on_change):
        captured["on_change"] = on_change

    monkeypatch.setattr("payload.cli.watch_loop", fake_watch_loop)
    result = runner.invoke(app, ["watch", "."])
    assert result.exit_code == 0

    captured["on_change"](proj / "ROW1.txt")
    out = capsys.readouterr().out
    assert "member of 'rows'" in out
    assert not (proj / "build" / "ROW1.bin").exists()
    assert (proj / "build" / "rows.bin").exists()


# --- pipeline show: remaining branches --------------------------------------

def test_pipeline_show_explicit_exec_stage_and_checkpoint_states(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "raw_text" }, '
        '{ type = "writer", name = "bin" }, '
        '{ type = "exec", command = "cp {input} {output}", output_extension = ".copy", on_error = "warn" }'
        ']\n'
    )

    before = runner.invoke(app, ["pipeline", "show", "example_table"])
    assert before.exit_code == 0
    assert "on_error=warn" in before.stdout
    assert "none" in before.stdout  # no checkpoint yet for the non-terminal writer stage

    runner.invoke(app, ["build", "example_table.raw"])

    after = runner.invoke(app, ["pipeline", "show", "example_table"])
    assert "valid" in after.stdout


# --- golden set/check/diff/clear ---------------------------------------------

def test_golden_set_defaults_to_latest_snapshot(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1"])

    result = runner.invoke(app, ["golden", "set", "example_table"])

    assert result.exit_code == 0
    assert "#1" in result.stdout


def test_golden_set_no_snapshot_yet_fails(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["golden", "set", "example_table"])

    assert result.exit_code != 0


def test_golden_check_single_table_match(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["golden", "check", "example_table"])

    assert result.exit_code == 0
    assert "match" in result.stdout


def test_golden_check_single_table_mismatch_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "build" / "example_table.bin").write_bytes(b"other content")

    result = runner.invoke(app, ["golden", "check", "example_table"])

    assert result.exit_code == 3


def test_golden_check_all_tables_reports_summary(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["golden", "check"])

    assert result.exit_code == 0
    assert "match" in result.stdout


def test_golden_diff_no_difference(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["golden", "diff", "example_table"])

    assert result.exit_code == 0
    assert "No difference" in result.stdout


def test_golden_diff_shows_byte_differences(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "build" / "example_table.bin").write_bytes(b"\xff\xff\xff\xff")

    result = runner.invoke(app, ["golden", "diff", "example_table"])

    assert result.exit_code == 0
    assert "Diff for" in result.stdout


def test_golden_clear_removes_pointer(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["golden", "clear", "example_table"])
    assert result.exit_code == 0

    after = runner.invoke(app, ["golden", "check", "example_table"])
    assert "not set" in after.stdout


def test_golden_clear_idempotent(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["golden", "clear", "example_table"])
    assert result.exit_code == 0
    assert "No golden set" in result.stdout


def test_golden_check_unknown_table_exits_4(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["golden", "check", "does_not_exist"])
    assert result.exit_code == 4


def test_golden_diff_unknown_table_exits_4(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["golden", "diff", "does_not_exist"])
    assert result.exit_code == 4


def test_golden_check_single_table_stale_raises(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "example_table.raw").write_text("0x99\n")

    result = runner.invoke(app, ["golden", "check", "example_table"])

    assert result.exit_code == 3


def test_golden_check_all_tables_exits_3_if_any_bad(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    (proj / "second.raw").write_text("0x02\n")
    runner.invoke(app, ["build-all"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])
    (proj / "build" / "second.bin").write_bytes(b"tampered")

    result = runner.invoke(app, ["golden", "check"])

    assert result.exit_code == 3
    assert "match" in result.stdout
    assert "mismatch" in result.stdout


def test_clean_golden_confirmation_prompt_declined(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["clean", "--target", "golden"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    after = runner.invoke(app, ["golden", "check", "example_table"])
    assert "match" in after.stdout  # untouched, confirmed cancelled


# --- plugin info -------------------------------------------------------------

def test_plugin_info_unknown_exits_4(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "info", "does_not_exist"])
    assert result.exit_code == 4


def test_plugin_info_reader_shows_extensions_and_default_writer(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "info", "raw_text"])
    assert result.exit_code == 0
    assert "extensions" in result.stdout
    assert "suggested writer" in result.stdout


def test_plugin_info_writer_shows_extension(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "info", "bin"])
    assert result.exit_code == 0
    assert "output extension" in result.stdout


def test_plugin_info_shows_compatible_readers_when_restricted(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    plugin_dir = proj / "local_plugins"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "picky.py").write_text(
        "class PickyWriter:\n"
        "    name = 'picky'\n"
        "    extension = '.picky'\n"
        "    api_version = '1.0'\n"
        "    compatible_readers = ['raw_text']\n"
        "    def emit(self, ir, out_path, config):\n"
        "        out_path.write_bytes(ir.data)\n"
        "        return out_path\n"
        "\n"
        "WRITER = PickyWriter\n"
    )

    result = runner.invoke(app, ["plugin", "info", "picky"])

    assert result.exit_code == 0
    assert "only compatible with" in result.stdout
    assert "raw_text" in result.stdout


# --- plugin install-deps: remaining branches --------------------------------

def test_plugin_install_deps_already_satisfied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "p.py"
    f.write_text('REQUIRES = ["json", "os"]\n')  # stdlib, always satisfied

    result = runner.invoke(app, ["plugin", "install-deps", str(f)])

    assert result.exit_code == 0
    assert "already installed" in result.stdout


def test_plugin_install_deps_declined_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')

    result = runner.invoke(app, ["plugin", "install-deps", str(f)], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout


def test_plugin_install_deps_yes_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')

    with patch("payload.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
        result = runner.invoke(app, ["plugin", "install-deps", str(f), "--yes"])

    assert result.exit_code == 0
    assert "installed" in result.stdout


def test_plugin_install_deps_pip_failure_exits_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "p.py"
    f.write_text('REQUIRES = ["nonexistent_library_xyz_123"]\n')

    with patch("payload.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
        result = runner.invoke(app, ["plugin", "install-deps", str(f), "--yes"])

    assert result.exit_code == 1
    assert "failed" in result.stdout


# --- plugin validate ---------------------------------------------------------

def test_plugin_validate_unknown_exits_4(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "validate", "does_not_exist"])
    assert result.exit_code == 4


def test_plugin_validate_reader_without_sample_skips_behavior_checks(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "validate", "raw_text"])
    assert result.exit_code == 0
    assert "skipping behavioral checks" in result.stdout


def test_plugin_validate_reader_with_sample_conforms(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    sample = proj / "sample.raw"
    sample.write_text("0x0A\n")

    result = runner.invoke(app, ["plugin", "validate", "raw_text", "--sample", str(sample)])

    assert result.exit_code == 0
    assert "conforms" in result.stdout


def test_plugin_validate_writer_conforms(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["plugin", "validate", "bin"])
    assert result.exit_code == 0
    assert "conforms" in result.stdout


def test_plugin_validate_non_conforming_plugin_exits_1(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    plugin_dir = proj / "local_plugins"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "incomplete.py").write_text(
        "class IncompleteReader:\n"
        "    name = 'incomplete'\n"
        "    extensions = ['.inc']\n"
        "    api_version = '1.0'\n"
        "    # missing 'sniff' and 'parse'\n"
        "\n"
        "READER = IncompleteReader\n"
    )

    result = runner.invoke(app, ["plugin", "validate", "incomplete"])

    assert result.exit_code == 1
    assert "violations" in result.stdout


# --- plugin new-local ----------------------------------------------------

def test_plugin_new_local_unknown_kind_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugin", "new-local", "foo", "--kind", "boh"])
    assert result.exit_code == 2


def test_plugin_new_local_creates_reader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugin", "new-local", "my_format", "--kind", "reader"])
    assert result.exit_code == 0
    created = tmp_path / "local_plugins" / "my_format.py"
    assert created.exists()
    assert "READER = MyFormatReader" in created.read_text()


def test_plugin_new_local_creates_writer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugin", "new-local", "my_format", "--kind", "writer"])
    assert result.exit_code == 0
    created = tmp_path / "local_plugins" / "my_format.py"
    assert "WRITER = MyFormatWriter" in created.read_text()


def test_plugin_new_local_creates_doctor_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugin", "new-local", "my_check", "--kind", "doctor-check"])
    assert result.exit_code == 0
    created = tmp_path / "local_plugins" / "my_check.py"
    assert "DOCTOR_CHECK = MyCheck" in created.read_text()


def test_plugin_new_local_refuses_existing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["plugin", "new-local", "dup", "--kind", "reader"])
    result = runner.invoke(app, ["plugin", "new-local", "dup", "--kind", "reader"])
    assert result.exit_code == 2
    assert "already exists" in result.stdout


# --- clean -----------------------------------------------------------------

def test_clean_unknown_target_exits_2(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["clean", "--target", "boh"])
    assert result.exit_code == 2


def test_clean_nothing_to_clean(tmp_path, monkeypatch):
    # 'init' already creates build/ (empty) but not .payload_cache/,
    # which is only created the first time the cache is saved
    _init_project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["clean", "--target", "cache"])
    assert result.exit_code == 0
    assert "Nothing to clean" in result.stdout


def test_clean_golden_target_clears_pointers_not_a_directory(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["clean", "--target", "golden", "--yes"])

    assert result.exit_code == 0
    after = runner.invoke(app, ["golden", "check", "example_table"])
    assert "not set" in after.stdout


def test_clean_declined_confirmation(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    result = runner.invoke(app, ["clean", "--target", "build"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert (proj / "build").exists()


def test_clean_yes_removes_target(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    result = runner.invoke(app, ["clean", "--target", "build", "--yes"])

    assert result.exit_code == 0
    assert not (proj / "build").exists()


def test_clean_all_target(tmp_path, monkeypatch):
    proj = _init_project(tmp_path, monkeypatch)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])
    runner.invoke(app, ["commit", "-m", "v1", "--golden"])

    result = runner.invoke(app, ["clean", "--target", "all", "--yes"])

    assert result.exit_code == 0
    assert not (proj / "build").exists()
    after = runner.invoke(app, ["golden", "check", "example_table"])
    assert "not set" in after.stdout


# --- init: remaining branches --------------------------------------------

def test_init_wizard_prompts_for_name_when_omitted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--wizard"], input="wizname\ny\ny\n\nlittle\nn\n")
    assert result.exit_code == 0
    assert (tmp_path / "wizname" / "table-tool.toml").exists()


def test_init_refuses_existing_nonempty_named_dir_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "already_exists"
    target.mkdir()
    (target / "something.txt").write_text("x")

    result = runner.invoke(app, ["init", "already_exists"])

    assert result.exit_code == 2
    assert "already exists" in result.stdout


def test_init_wizard_git_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("payload.cli.shutil.which", return_value=None):
        result = runner.invoke(
            app, ["init", "proj", "--wizard"],
            input="y\ny\n\nlittle\ny\n",
        )
    assert result.exit_code == 0
    assert "git not found" in result.stdout


def test_init_wizard_git_init_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("payload.cli.shutil.which", return_value="/usr/bin/git"), \
         patch("payload.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stderr="failed")):
        result = runner.invoke(
            app, ["init", "proj", "--wizard"],
            input="y\ny\n\nlittle\ny\n",
        )
    assert result.exit_code == 0
    assert "'git init' failed" in result.stdout


# --- command-line entry point ------------------------------------------------

def test_module_entry_point_runs_as_script():
    result = subprocess.run(
        [sys.executable, "-m", "payload.cli", "--version"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert "payload" in result.stdout.lower()
