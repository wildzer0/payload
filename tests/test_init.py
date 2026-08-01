from pathlib import Path

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
    assert not (target / "golden").exists()  # golden non è più una cartella


def test_init_project_does_not_overwrite_without_force(tmp_path):
    target = tmp_path / "proj"
    init_project(target)
    (target / "table-tool.toml").write_text("modificato a mano")

    init_project(target, force=False)
    assert (target / "table-tool.toml").read_text() == "modificato a mano"


def test_init_project_overwrites_with_force(tmp_path):
    target = tmp_path / "proj"
    init_project(target)
    (target / "table-tool.toml").write_text("modificato a mano")

    init_project(target, force=True)
    assert (target / "table-tool.toml").read_text() != "modificato a mano"
