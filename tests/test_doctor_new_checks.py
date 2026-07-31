import shutil
import subprocess
from pathlib import Path

import pytest

from payload.core.doctor import GitCheck, LocalPluginDepsCheck
from payload.core.plugin_base import CheckStatus


@pytest.mark.skipif(shutil.which("git") is None, reason="richiede git")
def test_git_check_ok_without_repo(tmp_path):
    result = GitCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK
    assert "non ancora un repo git" in result.message


@pytest.mark.skipif(shutil.which("git") is None, reason="richiede git")
def test_git_check_reports_repo_presence(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    result = GitCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK
    assert "repo git presente" in result.message


def test_local_plugin_deps_check_ok_when_no_plugins(tmp_path):
    result = LocalPluginDepsCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK


def test_local_plugin_deps_check_warns_on_missing_dependency(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text('REQUIRES = ["libreria_inesistente_xyz_123"]\n')

    result = LocalPluginDepsCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.WARN
    assert "libreria_inesistente_xyz_123" in result.hint


def test_local_plugin_deps_check_ok_when_deps_satisfied(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "good.py").write_text('REQUIRES = ["json", "os"]\n')  # stdlib, sempre soddisfatte

    result = LocalPluginDepsCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK
