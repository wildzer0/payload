from pathlib import Path

import pytest

from payload.core.errors import MissingPluginDependenciesError
from payload.core.local_plugins import (
    missing_requirements,
    read_requires_static,
)
from payload.core.plugin_base import CheckStatus
from payload.core.registry import PluginRegistry, load_plugins


def test_read_requires_static_extracts_list(tmp_path):
    f = tmp_path / "plugin.py"
    f.write_text('REQUIRES = ["numpy>=1.20", "pyserial"]\n\nclass X:\n    pass\n')

    assert read_requires_static(f) == ["numpy>=1.20", "pyserial"]


def test_read_requires_static_empty_if_absent(tmp_path):
    f = tmp_path / "plugin.py"
    f.write_text("class X:\n    pass\n")

    assert read_requires_static(f) == []


def test_read_requires_static_works_even_if_module_would_fail_to_import(tmp_path):
    """Il punto centrale del meccanismo: la lettura è statica (AST), non
    esecuzione — funziona anche se il modulo importerebbe qualcosa che
    non esiste, perché non lo eseguiamo mai per leggere REQUIRES."""
    f = tmp_path / "plugin.py"
    f.write_text(
        'import questo_modulo_non_esiste_di_sicuro\n'
        'REQUIRES = ["questo_modulo_non_esiste_di_sicuro"]\n'
    )

    assert read_requires_static(f) == ["questo_modulo_non_esiste_di_sicuro"]


def test_missing_requirements_detects_absent_package():
    missing = missing_requirements(["libreria_sicuramente_inesistente_xyz"])
    assert missing == ["libreria_sicuramente_inesistente_xyz"]


def test_missing_requirements_ignores_stdlib_present():
    missing = missing_requirements(["json", "os", "pathlib"])
    assert missing == []


def test_missing_requirements_handles_version_specifiers():
    missing = missing_requirements(["json>=1.0", "libreria_inesistente_xyz>=2.0"])
    assert missing == ["libreria_inesistente_xyz>=2.0"]


def test_load_plugins_strict_raises_on_missing_deps(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text(
        'REQUIRES = ["libreria_inesistente_xyz"]\n\n'
        'class W:\n    name = "w"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    with pytest.raises(MissingPluginDependenciesError):
        load_plugins(project_root=tmp_path, strict=True)


def test_load_plugins_non_strict_tracks_missing_deps_without_raising(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text(
        'REQUIRES = ["libreria_inesistente_xyz"]\n\n'
        'class W:\n    name = "w"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    registry = load_plugins(project_root=tmp_path, strict=False)

    assert "w" not in registry.writers
    assert len(registry.load_failures) == 1
    assert "libreria_inesistente_xyz" in registry.load_failures[0][2]


def test_plugin_with_satisfied_requires_loads_normally(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "good.py").write_text(
        'REQUIRES = ["json"]\n\n'
        'class W:\n    name = "good_writer"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    registry = load_plugins(project_root=tmp_path, strict=True)

    assert "good_writer" in registry.writers


def test_doctor_plugins_check_not_fail_for_missing_local_deps(tmp_path):
    """Regressione: una dipendenza mancante di un plugin locale non deve
    far FALLIRE il check 'plugins' — è già coperta con la severità
    giusta (WARN) da 'local_plugin_deps'. Contarla due volte con
    severità diverse sarebbe contraddittorio e romperebbe 'pld doctor'
    per un problema facilmente risolvibile con install-deps."""
    from payload.core.doctor import PluginLoadCheck

    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text('REQUIRES = ["libreria_inesistente_xyz"]\n')

    result = PluginLoadCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


def test_plugin_without_requires_unaffected(tmp_path):
    """Nessuna regressione per i plugin che non dichiarano REQUIRES."""
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "simple.py").write_text(
        'class W:\n    name = "simple_writer"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    registry = load_plugins(project_root=tmp_path, strict=True)

    assert "simple_writer" in registry.writers
