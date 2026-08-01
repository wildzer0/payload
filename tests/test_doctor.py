import subprocess
from pathlib import Path
from unittest.mock import patch

from payload.core.doctor import (
    CacheIntegrityCheck,
    ConfigValidityCheck,
    DirWritableCheck,
    GitCheck,
    LocalPluginDepsCheck,
    LocalPluginStubCheck,
    PipelineExecStagesCheck,
    PluginLoadCheck,
    TableNameUniquenessCheck,
    ToolchainCheck,
    builtin_checks,
    run_doctor,
)
from payload.core.plugin_base import CheckResult, CheckStatus
from payload.core.registry import PluginRegistry


# --- ToolchainCheck ---------------------------------------------------------

def test_toolchain_check_warns_when_not_configured():
    result = ToolchainCheck("compiler", "compiler").run({})
    assert result.status == CheckStatus.WARN
    assert "non configurato" in result.message


def test_toolchain_check_fails_when_binary_not_in_path():
    config = {"toolchain": {"compiler": "questo_binario_non_esiste_di_sicuro_xyz"}}
    result = ToolchainCheck("compiler", "compiler").run(config)
    assert result.status == CheckStatus.FAIL


def test_toolchain_check_ok_when_binary_found_and_responds():
    config = {"toolchain": {"compiler": "python3"}}
    result = ToolchainCheck("compiler", "compiler").run(config)
    assert result.status == CheckStatus.OK


def test_toolchain_check_warns_on_timeout():
    config = {"toolchain": {"compiler": "python3"}}
    with patch("payload.core.doctor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
        result = ToolchainCheck("compiler", "compiler").run(config)
    assert result.status == CheckStatus.WARN
    assert "non risponde" in result.message


def test_toolchain_check_fails_on_oserror():
    config = {"toolchain": {"compiler": "python3"}}
    with patch("payload.core.doctor.subprocess.run", side_effect=OSError("permesso negato")):
        result = ToolchainCheck("compiler", "compiler").run(config)
    assert result.status == CheckStatus.FAIL
    assert "non eseguibile" in result.message


# --- PluginLoadCheck ---------------------------------------------------------

def test_plugin_load_check_ok_with_no_local_plugins(tmp_path):
    result = PluginLoadCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK
    assert "plugin caricati" in result.message


def test_plugin_load_check_fails_on_broken_local_plugin(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "crashes.py").write_text('raise RuntimeError("boom")\n')

    result = PluginLoadCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.FAIL
    assert "crashes.py" in result.message
    assert "crashes.py" in result.hint


# --- ConfigValidityCheck -----------------------------------------------------

def test_config_validity_check_ok(tmp_path):
    result = ConfigValidityCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK
    assert "0 sidecar" in result.message


def test_config_validity_check_fails_on_malformed_global_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text("questo non e' toml valido [[[")
    result = ConfigValidityCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.FAIL
    assert "table-tool.toml" in result.hint


def test_config_validity_check_scans_and_flags_malformed_sidecar(tmp_path):
    (tmp_path / "esempio.config.toml").write_text("[defaults]\nwriter = 123\n")  # tipo sbagliato
    result = ConfigValidityCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.FAIL
    assert "esempio.config.toml" in result.hint


def test_config_validity_check_counts_valid_sidecars(tmp_path):
    (tmp_path / "esempio.config.toml").write_text("[defaults]\nwriter = \"bin\"\n")
    result = ConfigValidityCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK
    assert "1 sidecar" in result.message


# --- DirWritableCheck ---------------------------------------------------------

def test_dir_writable_check_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = DirWritableCheck().run({"defaults": {"output_dir": "build", "golden_dir": "golden", "cache_dir": ".cache"}})
    assert result.status == CheckStatus.OK


def test_dir_writable_check_resolves_relative_to_project_root_not_cwd(tmp_path, monkeypatch):
    """Regressione: con _project_root diverso dalla cwd del processo
    (es. 'pld serve' lanciato da una cartella diversa dal progetto
    servito), le directory vanno create sotto _project_root, MAI sotto
    la cwd — altrimenti il check inquina silenziosamente la cwd del
    processo server con directory vuote."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    project_root = tmp_path / "project"
    project_root.mkdir()

    result = DirWritableCheck().run({
        "_project_root": str(project_root),
        "defaults": {"output_dir": "build", "golden_dir": "golden", "cache_dir": ".cache"},
    })

    assert result.status == CheckStatus.OK
    assert (project_root / "build").exists()
    assert not (elsewhere / "build").exists()


def test_dir_writable_check_fails_when_path_is_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    blocked = tmp_path / "build"
    blocked.write_text("questo e' un file, non una cartella")

    result = DirWritableCheck().run({"defaults": {"output_dir": "build", "golden_dir": "golden", "cache_dir": ".cache"}})

    assert result.status == CheckStatus.FAIL
    assert "build" in result.message


# --- CacheIntegrityCheck ------------------------------------------------------

def test_cache_integrity_check_ok_no_cache(tmp_path):
    result = CacheIntegrityCheck().run({"defaults": {"cache_dir": str(tmp_path / "nope")}})
    assert result.status == CheckStatus.OK
    assert "Nessuna cache" in result.message


def test_cache_integrity_check_resolves_relative_cache_dir_against_project_root(tmp_path, monkeypatch):
    """Stessa regressione di DirWritableCheck: un cache_dir relativo va
    risolto rispetto a _project_root, non alla cwd del processo."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    project_root = tmp_path / "project"
    cache_dir = project_root / ".cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".payload_cache.json").write_text("{}")

    result = CacheIntegrityCheck().run({"_project_root": str(project_root), "defaults": {"cache_dir": ".cache"}})

    assert result.status == CheckStatus.OK
    assert "integra" in result.message


def test_cache_integrity_check_ok_valid_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".payload_cache.json").write_text("{}")
    result = CacheIntegrityCheck().run({"defaults": {"cache_dir": str(cache_dir)}})
    assert result.status == CheckStatus.OK
    assert "integra" in result.message


def test_cache_integrity_check_warns_on_corrupted_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".payload_cache.json").write_text("{not valid json")
    result = CacheIntegrityCheck().run({"defaults": {"cache_dir": str(cache_dir)}})
    assert result.status == CheckStatus.WARN
    assert "corrotta" in result.message


# --- TableNameUniquenessCheck --------------------------------------------------

def test_table_name_uniqueness_check_ok(tmp_path):
    (tmp_path / "a.raw").write_text("0x01\n")
    result = TableNameUniquenessCheck().run({"_project_root": str(tmp_path), "defaults": {"output_dir": "build"}})
    assert result.status == CheckStatus.OK


def test_table_name_uniqueness_check_fails_on_duplicates(tmp_path):
    (tmp_path / "sub1").mkdir()
    (tmp_path / "sub2").mkdir()
    (tmp_path / "sub1" / "sensor.raw").write_text("0x01\n")
    (tmp_path / "sub2" / "sensor.raw").write_text("0x02\n")

    result = TableNameUniquenessCheck().run({"_project_root": str(tmp_path), "defaults": {"output_dir": "build"}})

    assert result.status == CheckStatus.FAIL
    assert "sensor" in result.message


# --- LocalPluginDepsCheck (rami non coperti in test_doctor_new_checks.py) -----

def test_local_plugin_deps_check_ignores_underscore_prefixed_files(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "_helper.py").write_text('REQUIRES = ["libreria_inesistente_xyz"]\n')

    result = LocalPluginDepsCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


def test_local_plugin_deps_check_ignores_files_without_requires(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "senza_requires.py").write_text("class X:\n    pass\n")

    result = LocalPluginDepsCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


# --- LocalPluginStubCheck ------------------------------------------------------

def test_local_plugin_stub_check_ok_with_no_local_plugins(tmp_path):
    result = LocalPluginStubCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK


def test_local_plugin_stub_check_warns_on_scaffold(tmp_path):
    from payload.plugin_scaffold import scaffold_local_plugin

    scaffold_local_plugin("my_reader", "reader", tmp_path / "local_plugins")

    result = LocalPluginStubCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.WARN
    assert "my_reader.py" in result.message
    assert "parse" in result.message


def test_local_plugin_stub_check_ok_for_implemented_plugin(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "upper.py").write_text(
        "class X:\n"
        "    name = 'x'\n"
        "    extension = '.x'\n"
        "    api_version = '1.0'\n"
        "    def emit(self, ir, out_path, config):\n"
        "        out_path.write_bytes(ir.data)\n"
        "        return out_path\n"
        "WRITER = X\n"
    )

    result = LocalPluginStubCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


def test_local_plugin_stub_check_ignores_underscore_prefixed_files(tmp_path):
    plugin_dir = tmp_path / "local_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "_helper.py").write_text(
        "class X:\n"
        "    def run(self, config):\n"
        "        raise NotImplementedError\n"
    )

    result = LocalPluginStubCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.OK


# --- GitCheck (rami non coperti dai test con git reale) -----------------------

def test_git_check_warns_when_git_missing(tmp_path):
    with patch("payload.core.doctor.shutil.which", return_value=None):
        result = GitCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.WARN
    assert "non trovato" in result.message


def test_git_check_warns_on_timeout_or_oserror(tmp_path):
    with patch("payload.core.doctor.shutil.which", return_value="/usr/bin/git"), \
         patch("payload.core.doctor.subprocess.run", side_effect=OSError("boom")):
        result = GitCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.WARN
    assert "non risponde" in result.message


# --- PipelineExecStagesCheck ---------------------------------------------------

def test_pipeline_exec_check_ok_when_none_configured(tmp_path):
    result = PipelineExecStagesCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK


def test_pipeline_exec_check_warns_and_counts_across_files(tmp_path):
    (tmp_path / "table-tool.toml").write_text(
        '[pipeline]\nstages = ['
        '{ type = "reader", name = "a" }, '
        '{ type = "writer", name = "b" }, '
        '{ type = "exec", command = "x", output_extension = ".x" }'
        ']\n'
    )
    (tmp_path / "altro.config.toml").write_text(
        '[pipeline]\nstages = [{ type = "exec", command = "y", output_extension = ".y" }]\n'
    )

    result = PipelineExecStagesCheck().run({"_project_root": str(tmp_path)})

    assert result.status == CheckStatus.WARN
    assert "2 stage 'exec'" in result.message
    assert "2 file" in result.message


def test_pipeline_exec_check_skips_malformed_sidecar(tmp_path):
    (tmp_path / "rotto.config.toml").write_text("questo non e' toml [[[")
    result = PipelineExecStagesCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK  # il file malformato viene ignorato, non conta come errore qui


def test_pipeline_exec_check_tolerates_non_list_stages(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[pipeline]\nstages = "non una lista"\n')
    result = PipelineExecStagesCheck().run({"_project_root": str(tmp_path)})
    assert result.status == CheckStatus.OK


# --- builtin_checks / run_doctor ------------------------------------------------

def test_builtin_checks_returns_all_expected_checks():
    checks = builtin_checks()
    names = {c.name for c in checks}
    assert names == {
        "toolchain", "plugins", "config", "table_names",
        "local_plugin_deps", "local_plugin_stubs", "git", "pipeline_exec",
        "directories", "cache",
    }


def test_run_doctor_runs_all_builtin_checks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = run_doctor({"_project_root": str(tmp_path)})
    assert len(results) == len(builtin_checks())
    assert all(isinstance(r, CheckResult) for r in results)


def test_run_doctor_includes_registry_doctor_checks(tmp_path, monkeypatch):
    class _CustomCheck:
        name = "custom"
        api_version = "1.0"
        def run(self, config):
            return CheckResult(self.name, CheckStatus.OK, "tutto ok")

    monkeypatch.chdir(tmp_path)
    registry = PluginRegistry()
    registry.register_doctor_check(_CustomCheck())

    results = run_doctor({"_project_root": str(tmp_path)}, registry=registry)

    assert len(results) == len(builtin_checks()) + 1
    assert any(r.name == "custom" for r in results)


def test_run_doctor_converts_raw_exception_from_a_check_into_fail_result(tmp_path, monkeypatch):
    """Regressione trovata dall'utente: un doctor check di terze parti
    (es. uno scaffold locale con 'raise NotImplementedError' al posto
    di un run() vero) non deve far esplodere 'pld doctor'/'GET
    /api/doctor' — deve diventare un FAIL leggibile fra gli altri
    risultati, lasciando visibili tutti gli altri check."""
    class _CrashingCheck:
        name = "crashing"
        api_version = "1.0"

        def run(self, config):
            raise NotImplementedError("TODO: implementa il check")

    monkeypatch.chdir(tmp_path)
    registry = PluginRegistry()
    registry.register_doctor_check(_CrashingCheck())

    results = run_doctor({"_project_root": str(tmp_path)}, registry=registry)

    assert len(results) == len(builtin_checks()) + 1
    crashed = next(r for r in results if r.name == "crashing")
    assert crashed.status == CheckStatus.FAIL
    assert "NotImplementedError" in crashed.message
    assert crashed.hint is not None
