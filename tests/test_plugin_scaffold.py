from pathlib import Path

from payload.plugin_scaffold import scaffold_plugin


def test_scaffold_reader_creates_expected_structure(tmp_path):
    out = scaffold_plugin("payload-reader-test", "reader", tmp_path)

    assert out == tmp_path / "payload-reader-test"
    assert (out / "pyproject.toml").exists()
    assert (out / "src" / "payload_reader_test" / "plugin.py").exists()
    assert (out / "src" / "payload_reader_test" / "__init__.py").exists()
    assert (out / "tests" / "test_plugin.py").exists()


def test_scaffold_writer_uses_writer_group_and_suffix(tmp_path):
    out = scaffold_plugin("payload-writer-hex2", "writer", tmp_path)

    pyproject = (out / "pyproject.toml").read_text()
    assert 'payload.writers' in pyproject
    plugin_py = (out / "src" / "payload_writer_hex2" / "plugin.py").read_text()
    assert "Writer" in plugin_py  # nome classe finisce con 'Writer'


def test_scaffold_class_name_is_capitalized_correctly(tmp_path):
    out = scaffold_plugin("payload-reader-my-format", "reader", tmp_path)

    plugin_py = (out / "src" / "payload_reader_my_format" / "plugin.py").read_text()
    assert "class MyFormatReader" in plugin_py


def test_scaffold_no_placeholders_left_unrendered(tmp_path):
    out = scaffold_plugin("payload-reader-clean", "reader", tmp_path)

    for f in out.rglob("*"):
        if f.is_file():
            content = f.read_text()
            assert "{{" not in content, f"placeholder non renderizzato in {f}"


def test_scaffold_invalid_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        scaffold_plugin("payload-x-y", "not-a-real-kind", Path("/tmp"))
