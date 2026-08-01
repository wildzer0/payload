from pathlib import Path

from payload.core.config import load_config
from payload.init_cmd import init_project, is_nonempty_existing_dir


def test_empty_dir_is_not_flagged(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert is_nonempty_existing_dir(empty) is False


def test_nonempty_dir_is_flagged(tmp_path):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("x")
    assert is_nonempty_existing_dir(busy) is True


def test_nonexistent_dir_is_not_flagged(tmp_path):
    missing = tmp_path / "does-not-exist-yet"
    assert is_nonempty_existing_dir(missing) is False


def test_init_project_creates_target_dir_from_scratch(tmp_path):
    target = tmp_path / "new-project"
    assert not target.exists()

    init_project(target)

    assert target.is_dir()
    assert (target / "table-tool.toml").exists()
    assert (target / "example_table.raw").exists()
    assert (target / "build").is_dir()
    assert not (target / "golden").exists()  # golden is no longer a folder


def test_init_project_does_not_overwrite_without_force(tmp_path):
    target = tmp_path / "proj"
    init_project(target)
    (target / "table-tool.toml").write_text("modified by hand")

    init_project(target, force=False)
    assert (target / "table-tool.toml").read_text() == "modified by hand"


def test_init_project_overwrites_with_force(tmp_path):
    target = tmp_path / "proj"
    init_project(target)
    (target / "table-tool.toml").write_text("modified by hand")

    init_project(target, force=True)
    assert (target / "table-tool.toml").read_text() != "modified by hand"


def test_init_project_defaults_name_to_target_dir_basename(tmp_path):
    target = tmp_path / "sensor-calibration"

    init_project(target)

    assert load_config(target).project.name == "sensor-calibration"


def test_init_project_accepts_explicit_name_and_description(tmp_path):
    target = tmp_path / "proj"

    init_project(target, project_name="Sensor Calibration", project_description="test bench data acquisition")

    config = load_config(target)
    assert config.project.name == "Sensor Calibration"
    assert config.project.description == "test bench data acquisition"
