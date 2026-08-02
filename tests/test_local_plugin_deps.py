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
    """The core point of the mechanism: reading is static (AST), not
    execution — it works even if the module would import something
    that doesn't exist, because we never execute it to read REQUIRES."""
    f = tmp_path / "plugin.py"
    f.write_text(
        'import this_module_definitely_does_not_exist\n'
        'REQUIRES = ["this_module_definitely_does_not_exist"]\n'
    )

    assert read_requires_static(f) == ["this_module_definitely_does_not_exist"]


def test_read_requires_static_returns_empty_on_syntax_error(tmp_path):
    f = tmp_path / "plugin.py"
    f.write_text("this is not valid python [[[")

    assert read_requires_static(f) == []


def test_missing_requirements_skips_unparseable_requirement_string():
    # a string that doesn't start with a valid character for a
    # package name (here a specifier with no name) doesn't produce a
    # match and is simply ignored, not treated as a missing dependency
    missing = missing_requirements([">=1.0"])
    assert missing == []


def test_missing_requirements_detects_absent_package():
    missing = missing_requirements(["library_definitely_nonexistent_xyz"])
    assert missing == ["library_definitely_nonexistent_xyz"]


def test_missing_requirements_ignores_stdlib_present():
    missing = missing_requirements(["json", "os", "pathlib"])
    assert missing == []


def test_missing_requirements_handles_version_specifiers():
    missing = missing_requirements(["json>=1.0", "nonexistent_library_xyz>=2.0"])
    assert missing == ["nonexistent_library_xyz>=2.0"]


def test_load_plugins_strict_raises_on_missing_deps(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text(
        'REQUIRES = ["nonexistent_library_xyz"]\n\n'
        'class W:\n    name = "w"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    with pytest.raises(MissingPluginDependenciesError):
        load_plugins(project_root=tmp_path, strict=True)


def test_load_plugins_non_strict_tracks_missing_deps_without_raising(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text(
        'REQUIRES = ["nonexistent_library_xyz"]\n\n'
        'class W:\n    name = "w"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    registry = load_plugins(project_root=tmp_path, strict=False)

    assert "w" not in registry.writers
    assert len(registry.load_failures) == 1
    assert "nonexistent_library_xyz" in registry.load_failures[0][2]


def test_plugin_with_satisfied_requires_loads_normally(tmp_path):
    plugin_dir = tmp_path / "plugins"
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
    """Regression: a missing dependency of a local plugin must not
    make the 'plugins' check FAIL — it's already covered with the
    right severity (WARN) by 'local_plugin_deps'. Counting it twice
    with different severities would be contradictory and would break
    'pld doctor' for a problem easily fixed with install-deps."""
    from payload.core.doctor import PluginLoadCheck

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text('REQUIRES = ["nonexistent_library_xyz"]\n')

    result = PluginLoadCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


def test_plugin_without_requires_unaffected(tmp_path):
    """No regression for plugins that don't declare REQUIRES."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "simple.py").write_text(
        'class W:\n    name = "simple_writer"\n    extension = ".w"\n    api_version = "1.0"\n'
        '    def emit(self, ir, out_path, config):\n        return out_path\n\n'
        'WRITER = W\n'
    )

    registry = load_plugins(project_root=tmp_path, strict=True)

    assert "simple_writer" in registry.writers
