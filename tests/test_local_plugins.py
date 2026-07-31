import os
from pathlib import Path

import pytest

from payload.core.errors import PluginLoadError
from payload.core.local_plugins import discover_local_plugin_dirs, load_local_plugins
from payload.core.registry import PluginRegistry, load_plugins


WRITER_SOURCE = '''
class UpperWriter:
    name = "upper"
    extension = ".upper"
    api_version = "1.0"

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data.upper())
        return out_path

WRITER = UpperWriter
'''

MULTI_READER_SOURCE = '''
from payload.core.ir import TableIR

class ReaderA:
    name = "reader_a"
    extensions = [".a"]
    api_version = "1.0"
    def sniff(self, p): return False
    def parse(self, p, c): return TableIR(name=p.stem, data=b"a", source_path=p, source_format=self.name)

class ReaderB:
    name = "reader_b"
    extensions = [".b"]
    api_version = "1.0"
    def sniff(self, p): return False
    def parse(self, p, c): return TableIR(name=p.stem, data=b"b", source_path=p, source_format=self.name)

READERS = [ReaderA, ReaderB]
'''

BROKEN_SOURCE = '''
class BrokenWriter:
    name = "broken"
    extension = ".x"
    api_version = "1.0"
    def __init__(self):
        raise RuntimeError("init fallito")

WRITER = BrokenWriter
'''

# fallisce all'IMPORT del modulo stesso (non nell'istanziare una classe
# valida) — diverso da BROKEN_SOURCE, che invece esercita _register_one
MODULE_LEVEL_FAILURE_SOURCE = '''
raise RuntimeError("fallito prima ancora di definire un plugin")
'''


def test_local_plugins_dir_discovered_next_to_project_root(tmp_path):
    (tmp_path / "local_plugins").mkdir()
    dirs = discover_local_plugin_dirs(tmp_path)
    assert tmp_path / "local_plugins" in dirs


def test_no_local_plugins_dir_returns_empty(tmp_path):
    assert discover_local_plugin_dirs(tmp_path) == []


def test_env_var_adds_extra_directory(tmp_path, monkeypatch):
    external = tmp_path / "elsewhere"
    external.mkdir()
    monkeypatch.setenv("PAYLOAD_PLUGIN_PATH", str(external))

    dirs = discover_local_plugin_dirs(tmp_path)
    assert external in dirs


def test_env_var_nonexistent_directory_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYLOAD_PLUGIN_PATH", str(tmp_path / "non_esiste"))

    dirs = discover_local_plugin_dirs(tmp_path)
    assert dirs == []


def test_single_writer_loaded_and_functional(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "upper.py").write_text(WRITER_SOURCE)

    registry = PluginRegistry()
    load_local_plugins(tmp_path, registry, strict=True)

    assert "upper" in registry.writers

    from payload.core.ir import TableIR
    ir = TableIR(name="t", data=b"ciao", source_path=Path("x"), source_format="fake")
    out = tmp_path / "out.upper"
    registry.writers["upper"].emit(ir, out, {})
    assert out.read_bytes() == b"CIAO"


def test_plural_readers_all_loaded(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "multi.py").write_text(MULTI_READER_SOURCE)

    registry = PluginRegistry()
    load_local_plugins(tmp_path, registry, strict=True)

    assert set(registry.readers.keys()) == {"reader_a", "reader_b"}


def test_broken_plugin_strict_raises(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.py").write_text(BROKEN_SOURCE)

    registry = PluginRegistry()
    with pytest.raises(PluginLoadError):
        load_local_plugins(tmp_path, registry, strict=True)


def test_broken_plugin_non_strict_tracked_not_raised(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.py").write_text(BROKEN_SOURCE)

    registry = PluginRegistry()
    load_local_plugins(tmp_path, registry, strict=False)

    assert "broken" not in registry.writers
    assert len(registry.load_failures) == 1


def test_module_import_failure_strict_raises(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "crashes.py").write_text(MODULE_LEVEL_FAILURE_SOURCE)

    registry = PluginRegistry()
    with pytest.raises(PluginLoadError):
        load_local_plugins(tmp_path, registry, strict=True)


def test_module_import_failure_non_strict_tracked_not_raised(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "crashes.py").write_text(MODULE_LEVEL_FAILURE_SOURCE)

    registry = PluginRegistry()
    load_local_plugins(tmp_path, registry, strict=False)  # non deve sollevare

    assert len(registry.load_failures) == 1
    assert registry.load_failures[0][0] == "crashes.py"


def test_underscore_prefixed_files_ignored(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "_helper.py").write_text("questo non e python valido [[[")

    registry = PluginRegistry()
    load_local_plugins(tmp_path, registry, strict=True)  # non deve sollevare
    assert registry.load_failures == []


def test_load_plugins_integrates_local_plugins(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "upper.py").write_text(WRITER_SOURCE)

    registry = load_plugins(project_root=tmp_path, strict=False)
    assert "upper" in registry.writers


def test_registry_warns_on_name_collision(tmp_path, caplog):
    import logging

    registry = PluginRegistry()

    class FakeWriter:
        name = "dup"
        extension = ".x"
        api_version = "1.0"

        def emit(self, ir, out_path, config):
            return out_path

    with caplog.at_level(logging.WARNING):
        registry.register_writer(FakeWriter())
        registry.register_writer(FakeWriter())

    assert any("già registrato" in r.message for r in caplog.records)
