import os
from pathlib import Path

import pytest

from payload.core.errors import PluginLoadError
from payload.core.local_plugins import (
    discover_local_plugin_dirs,
    extract_plugin_classes,
    find_stub_methods,
    list_local_plugin_files,
    load_local_plugins,
    load_module_from_file,
)
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


# --- load_module_from_file / extract_plugin_classes / list_local_plugin_files (editor web) ---


def test_load_module_from_file_and_extract_plugin_classes(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    path = plugin_dir / "upper.py"
    path.write_text(WRITER_SOURCE)

    module = load_module_from_file(path)
    classes = extract_plugin_classes(module)

    assert classes == [("writer", module.UpperWriter)]


def test_extract_plugin_classes_multi_reader(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    path = plugin_dir / "multi.py"
    path.write_text(MULTI_READER_SOURCE)

    module = load_module_from_file(path)
    classes = extract_plugin_classes(module)

    assert [kind for kind, _ in classes] == ["reader", "reader"]


def test_list_local_plugin_files_no_dir_returns_empty(tmp_path):
    assert list_local_plugin_files(tmp_path) == []


def test_list_local_plugin_files_excludes_underscore_prefixed(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "upper.py").write_text(WRITER_SOURCE)
    (plugin_dir / "_helper.py").write_text("x = 1\n")

    files = list_local_plugin_files(tmp_path)

    assert [f.name for f in files] == ["upper.py"]


# --- find_stub_methods (scaffold non implementato) ---------------------------


def test_find_stub_methods_detects_scaffolded_reader(tmp_path):
    from payload.plugin_scaffold import scaffold_local_plugin

    path = scaffold_local_plugin("my_reader", "reader", tmp_path)
    assert find_stub_methods(path) == ["parse"]


def test_find_stub_methods_detects_scaffolded_writer(tmp_path):
    from payload.plugin_scaffold import scaffold_local_plugin

    path = scaffold_local_plugin("my_writer", "writer", tmp_path)
    assert find_stub_methods(path) == ["emit"]


def test_find_stub_methods_detects_scaffolded_doctor_check(tmp_path):
    from payload.plugin_scaffold import scaffold_local_plugin

    path = scaffold_local_plugin("my_check", "doctor-check", tmp_path)
    assert find_stub_methods(path) == ["run"]


def test_find_stub_methods_empty_for_implemented_plugin(tmp_path):
    path = tmp_path / "upper.py"
    path.write_text(WRITER_SOURCE)
    assert find_stub_methods(path) == []


def test_find_stub_methods_ignores_unrelated_method_names(tmp_path):
    """Solo parse/emit/run contano — un metodo helper qualsiasi che
    solleva NotImplementedError non è un plugin non implementato."""
    path = tmp_path / "plugin.py"
    path.write_text(
        "class X:\n"
        "    def sniff(self, path):\n"
        "        raise NotImplementedError\n"
        "    def emit(self, ir, out_path, config):\n"
        "        out_path.write_bytes(ir.data)\n"
        "        return out_path\n"
    )
    assert find_stub_methods(path) == []


def test_find_stub_methods_detects_bare_raise_without_call(tmp_path):
    """'raise NotImplementedError' (senza parentesi/messaggio) è
    equivalente a scopo di rilevamento — non solo la forma con Call
    generata dallo scaffold."""
    path = tmp_path / "plugin.py"
    path.write_text(
        "class X:\n"
        "    def run(self, config):\n"
        "        raise NotImplementedError\n"
    )
    assert find_stub_methods(path) == ["run"]


def test_find_stub_methods_ignores_multi_statement_body(tmp_path):
    """Un metodo con logica reale prima di un eventuale raise non è
    uno stub, anche se finisce comunque per sollevare qualcosa."""
    path = tmp_path / "plugin.py"
    path.write_text(
        "class X:\n"
        "    def run(self, config):\n"
        "        x = 1\n"
        "        raise NotImplementedError('mai completato')\n"
    )
    assert find_stub_methods(path) == []


def test_find_stub_methods_returns_empty_on_syntax_error(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("questo non e' python valido [[[")
    assert find_stub_methods(path) == []


def test_find_stub_methods_skips_leading_docstring_before_raise(tmp_path):
    """Lo scaffold non ha una docstring dentro il metodo, ma un utente
    che parte da zero potrebbe aggiungerne una prima del raise — non
    deve impedire il rilevamento dello stub."""
    path = tmp_path / "plugin.py"
    path.write_text(
        "class X:\n"
        "    def run(self, config):\n"
        "        '''TODO: descrivi cosa fa questo check.'''\n"
        "        raise NotImplementedError('TODO')\n"
    )
    assert find_stub_methods(path) == ["run"]


def test_find_stub_methods_ignores_raise_of_non_name_expression(tmp_path):
    """Un raise la cui espressione non è né un Name né una Call(Name)
    (es. un attributo tipo 'module.SomeError(...)') non viene
    riconosciuto come NotImplementedError — comportamento conservativo,
    nessun falso positivo."""
    path = tmp_path / "plugin.py"
    path.write_text(
        "import errors\n"
        "class X:\n"
        "    def run(self, config):\n"
        "        raise errors.SomeError('boom')\n"
    )
    assert find_stub_methods(path) == []
