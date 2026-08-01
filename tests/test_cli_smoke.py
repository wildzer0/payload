"""
Test a livello CLI, non solo core: usano typer.testing.CliRunner per
invocare i comandi come farebbe davvero un utente da terminale — un
bug come quello di 'typer.Exit catturato come errore interno' (vedi
test_doctor_missing_toolchain_exits_cleanly_no_traceback) sarebbe stato
beccato subito da questi test, non dai test a livello core.

NOTA: scritti senza poter eseguire typer in questo ambiente di sviluppo
(nessun accesso di rete per installarlo) — verificati con cura contro
l'API documentata, ma vale la pena un doppio controllo lanciandoli
davvero con `pytest tests/test_cli_smoke.py -v`.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from payload.cli import app

runner = CliRunner()


def test_init_wizard_yes_uses_all_defaults(tmp_path, monkeypatch):
    """--wizard --yes: nessuna domanda posta, tutti i default usati."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "wizproj", "--wizard", "--yes"])

    assert result.exit_code == 0
    proj = tmp_path / "wizproj"
    assert (proj / "table-tool.toml").exists()
    assert (proj / "local_plugins").is_dir()
    assert (proj / "example_table.raw").exists()
    assert not (proj / ".git").exists()  # do_git_init default False


def test_init_local_plugins_created_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "proj"])
    assert result.exit_code == 0
    assert (tmp_path / "proj" / "local_plugins").is_dir()
    assert (tmp_path / "proj" / "local_plugins" / "README.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="richiede git")
def test_init_wizard_can_init_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # simula le risposte al wizard: nome vuoto (usa quello posizionale),
    # poi tutte le domande in ordine: local_plugins, example, writer,
    # byte_order, git init
    result = runner.invoke(
        app, ["init", "gitproj", "--wizard"],
        input="y\ny\n\nlittle\ny\n",
    )
    assert result.exit_code == 0
    assert (tmp_path / "gitproj" / ".git").is_dir()


def test_doctor_reports_git_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    monkeypatch.chdir(tmp_path / "proj")

    result = runner.invoke(app, ["doctor"])

    assert "git" in result.stdout.lower()


def test_plugin_install_deps_no_requires(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugin_file = tmp_path / "no_requires.py"
    plugin_file.write_text("class X:\n    pass\n")

    result = runner.invoke(app, ["plugin", "install-deps", str(plugin_file)])

    assert result.exit_code == 0
    assert "niente da installare" in result.stdout


def test_local_plugin_with_requires_reported_in_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    (proj / "local_plugins" / "needs_fake.py").write_text(
        'REQUIRES = ["libreria_inesistente_xyz"]\n'
    )
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["doctor"])

    assert "local_plugin_deps" in result.stdout or "dipendenze mancanti" in result.stdout


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


def test_help_does_not_crash():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_init_creates_project_in_new_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "myproj"])
    assert result.exit_code == 0
    assert (tmp_path / "myproj" / "table-tool.toml").exists()
    assert (tmp_path / "myproj" / "example_table.raw").exists()


def test_init_refuses_nonempty_cwd_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing_file.txt").write_text("x")

    result = runner.invoke(app, ["init"], input="n\n")

    assert result.exit_code == 0
    assert not (tmp_path / "table-tool.toml").exists()


def test_build_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    assert result.exit_code == 0
    assert (proj / "build" / "example_table.bin").exists()


def test_pipeline_show_implicit_pipeline(tmp_path, monkeypatch):
    """pld pipeline show su una tabella senza [pipeline] esplicita deve
    mostrare comunque la pipeline implicita a 2 stage (reader+writer)."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["pipeline", "show", "example_table"])

    assert result.exit_code == 0
    assert "raw_text" in result.stdout
    assert "bin" in result.stdout


def test_pipeline_show_unknown_table_exits_4(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    monkeypatch.chdir(tmp_path / "proj")

    result = runner.invoke(app, ["pipeline", "show", "tabella_inesistente"])

    assert result.exit_code == 4


def test_status_shows_never_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    monkeypatch.chdir(tmp_path / "proj")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "mai salvata" in result.stdout


def test_commit_and_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    commit_result = runner.invoke(app, ["commit", "-m", "primo commit"])
    assert commit_result.exit_code == 0

    log_result = runner.invoke(app, ["log", "example_table"])
    assert log_result.exit_code == 0
    assert "primo commit" in log_result.stdout


def test_doctor_missing_toolchain_exits_cleanly_no_traceback(tmp_path, monkeypatch):
    """Test di regressione per il bug 'typer.Exit catturato come errore
    interno': con un compilatore inesistente, doctor deve uscire con
    l'exit code giusto SENZA 'Errore interno inatteso' né traceback."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    (proj / "table-tool.toml").write_text(
        '[toolchain]\ncompiler = "questo-compilatore-non-esiste-di-sicuro-xyz"\n'
    )
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "Errore interno inatteso" not in result.stdout
    assert "Traceback" not in result.stdout


def test_plugin_new_creates_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugin", "new", "payload-writer-testx", "--kind", "writer"])
    assert result.exit_code == 0
    assert (tmp_path / "payload-writer-testx" / "pyproject.toml").exists()


def test_plugins_list_runs_without_error():
    result = runner.invoke(app, ["plugins"])
    assert result.exit_code == 0


def test_golden_check_missing_exits_with_expected_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"
    monkeypatch.chdir(proj)
    runner.invoke(app, ["build", "example_table.raw", "--to", "bin"])

    result = runner.invoke(app, ["golden", "check", "example_table"])

    assert result.exit_code == 0  # 'missing' non è un errore bloccante per golden check
    assert "non impostato" in result.stdout or "!" in result.stdout


@pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("objcopy") is None,
    reason="richiede gcc e objcopy reali",
)
def test_c_source_to_obj_via_cli(tmp_path, monkeypatch):
    """Verifica end-to-end della pipeline c_source -> obj passando
    davvero dalla CLI, non chiamando i plugin direttamente."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "proj"])
    proj = tmp_path / "proj"

    info = subprocess.run(["objcopy", "--info"], capture_output=True, text=True)
    output = info.stdout + info.stderr
    target = "elf64-x86-64" if "elf64-x86-64" in output else "elf32-i386"
    arch = "i386:x86-64" if target == "elf64-x86-64" else "i386"

    (proj / "table-tool.toml").write_text(
        f'[toolchain]\ncompiler = "gcc"\nobjcopy = "objcopy"\n'
        f'objcopy_target = "{target}"\nobjcopy_arch = "{arch}"\n'
    )
    c_file = proj / "sensor.c"
    c_file.write_text(
        '#include <stdint.h>\n'
        'const uint8_t table_data[] __attribute__((section("payload_table_data"))) '
        '= {0xAA, 0xBB};\n'
    )
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["build", "sensor.c"])

    assert result.exit_code == 0
    assert (proj / "build" / "sensor.o").exists()
