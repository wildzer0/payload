import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.errors import PluginAlreadyExistsError, PluginSourceNotFoundError
from payload.core.plugin_install import install_plugin, install_plugin_from_bytes

_SOURCE = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "raw_text.py"
_WRITER_SOURCE = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "bin_writer.py"


def test_install_from_local_path_creates_file_and_dest_dir(tmp_path):
    dest = tmp_path / "plugins"
    result = install_plugin(dest, str(_SOURCE))

    assert result.path == dest / "raw_text.py"
    assert result.filename == "raw_text.py"
    assert result.path.read_bytes() == _SOURCE.read_bytes()
    assert result.sanity_ok is True
    assert result.kinds == ["reader"]


def test_install_refuses_to_overwrite_existing_file(tmp_path):
    dest = tmp_path / "plugins"
    dest.mkdir()
    existing = dest / "raw_text.py"
    existing.write_text("original content")

    with pytest.raises(PluginAlreadyExistsError):
        install_plugin(dest, str(_SOURCE))

    assert existing.read_text() == "original content"  # untouched


def test_install_overwrite_true_replaces_existing_file(tmp_path):
    dest = tmp_path / "plugins"
    dest.mkdir()
    existing = dest / "raw_text.py"
    existing.write_text("original content")

    result = install_plugin(dest, str(_SOURCE), overwrite=True)

    assert result.path.read_bytes() == _SOURCE.read_bytes()


def test_install_as_name_overrides_destination_filename(tmp_path):
    dest = tmp_path / "plugins"
    result = install_plugin(dest, str(_SOURCE), as_name="custom_name.py")

    assert result.filename == "custom_name.py"
    assert (dest / "custom_name.py").exists()
    assert not (dest / "raw_text.py").exists()


def test_install_missing_local_source_raises(tmp_path):
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin(tmp_path / "plugins", str(tmp_path / "does_not_exist.py"))


def test_install_rejects_non_py_local_source(tmp_path):
    bad = tmp_path / "not_a_plugin.txt"
    bad.write_text("hello")
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin(tmp_path / "plugins", str(bad))


def test_install_rejects_non_py_url():
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin(Path("/tmp/plugins"), "https://example.com/not_a_plugin.txt")


def test_install_from_url_fetches_and_writes_bytes(tmp_path):
    dest = tmp_path / "plugins"
    content = _SOURCE.read_bytes()

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return content

    with patch("payload.core.plugin_install.urllib.request.urlopen", return_value=_FakeResponse()):
        result = install_plugin(dest, "https://example.com/plugins/raw_text.py")

    assert result.path.read_bytes() == content
    assert result.filename == "raw_text.py"


def test_install_from_url_network_failure_raises_source_not_found(tmp_path):
    with patch("payload.core.plugin_install.urllib.request.urlopen", side_effect=urllib.error.URLError("no route to host")):
        with pytest.raises(PluginSourceNotFoundError):
            install_plugin(tmp_path / "plugins", "https://example.com/plugins/raw_text.py")


def test_install_url_without_derivable_filename_requires_as_name(tmp_path):
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin(tmp_path / "plugins", "https://example.com/")


def test_install_sanity_check_reports_issues_without_undoing_write(tmp_path):
    source = tmp_path / "no_plugin_here.py"
    source.write_text("x = 1\n")  # valid Python, but no READER/WRITER/DOCTOR_CHECK
    dest = tmp_path / "plugins"

    result = install_plugin(dest, str(source))

    assert result.sanity_ok is False
    assert result.kinds == []
    assert result.sanity_issues
    assert result.path.exists()  # still installed despite the failed sanity check


def test_install_sanity_check_reports_syntax_error_without_crashing(tmp_path):
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n")
    dest = tmp_path / "plugins"

    result = install_plugin(dest, str(source))

    assert result.sanity_ok is False
    assert result.path.exists()


def test_install_writer_plugin_reports_writer_kind(tmp_path):
    result = install_plugin(tmp_path / "plugins", str(_WRITER_SOURCE))
    assert result.kinds == ["writer"]


# --- install_plugin_from_bytes (browser upload / drag&drop) -----------------

def test_install_from_bytes_creates_file_and_dest_dir(tmp_path):
    dest = tmp_path / "plugins"
    content = _SOURCE.read_bytes()

    result = install_plugin_from_bytes(dest, "raw_text.py", content)

    assert result.path == dest / "raw_text.py"
    assert result.path.read_bytes() == content
    assert result.sanity_ok is True
    assert result.kinds == ["reader"]


def test_install_from_bytes_refuses_to_overwrite_existing_file(tmp_path):
    dest = tmp_path / "plugins"
    dest.mkdir()
    existing = dest / "raw_text.py"
    existing.write_text("original content")

    with pytest.raises(PluginAlreadyExistsError):
        install_plugin_from_bytes(dest, "raw_text.py", _SOURCE.read_bytes())

    assert existing.read_text() == "original content"


def test_install_from_bytes_overwrite_true_replaces_existing_file(tmp_path):
    dest = tmp_path / "plugins"
    dest.mkdir()
    existing = dest / "raw_text.py"
    existing.write_text("original content")

    result = install_plugin_from_bytes(dest, "raw_text.py", _SOURCE.read_bytes(), overwrite=True)

    assert result.path.read_bytes() == _SOURCE.read_bytes()


def test_install_from_bytes_rejects_non_py_filename(tmp_path):
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin_from_bytes(tmp_path / "plugins", "not_a_plugin.txt", b"x")


def test_install_from_bytes_rejects_unsafe_filename(tmp_path):
    with pytest.raises(PluginSourceNotFoundError):
        install_plugin_from_bytes(tmp_path / "plugins", "../escape.py", b"x")
