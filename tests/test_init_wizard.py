from pathlib import Path

from payload.core.config import load_config
from payload.init_cmd import init_project


def test_default_call_uses_static_template_with_bin(tmp_path):
    init_project(tmp_path)
    config = load_config(tmp_path)
    assert config.defaults.writer == "bin"  # historical behavior, on purpose


def test_local_plugins_dir_created_by_default(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / "local_plugins").is_dir()
    assert (tmp_path / "local_plugins" / "README.md").exists()


def test_local_plugins_dir_can_be_disabled(tmp_path):
    init_project(tmp_path, include_local_plugins=False)
    assert not (tmp_path / "local_plugins").exists()


def test_example_table_can_be_disabled(tmp_path):
    init_project(tmp_path, include_example=False)
    assert not (tmp_path / "example_table.raw").exists()


def test_explicit_writer_and_byte_order_respected(tmp_path):
    init_project(tmp_path, writer="hex", byte_order="big")
    config = load_config(tmp_path)
    assert config.defaults.writer == "hex"
    assert config.defaults.byte_order == "big"


def test_explicit_none_writer_is_respected_not_defaulted_to_bin(tmp_path):
    """The case that revealed the sentinel bug: explicitly passing
    writer=None (as the wizard would when the user expresses no
    preference) must REALLY give None, not silently 'bin'."""
    init_project(tmp_path, writer=None, byte_order="little")
    config = load_config(tmp_path)
    assert config.defaults.writer is None


def test_force_overwrites_existing_local_plugins_readme(tmp_path):
    init_project(tmp_path)
    readme = tmp_path / "local_plugins" / "README.md"
    readme.write_text("modified by hand")

    init_project(tmp_path, force=True)
    assert readme.read_text() != "modified by hand"


def test_no_force_does_not_overwrite_local_plugins_readme(tmp_path):
    init_project(tmp_path)
    readme = tmp_path / "local_plugins" / "README.md"
    readme.write_text("modified by hand")

    init_project(tmp_path, force=False)
    assert readme.read_text() == "modified by hand"
