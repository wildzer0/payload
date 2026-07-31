"""
Check builtin per 'pld doctor'. Ogni check è indipendente e non blocca
gli altri se fallisce — l'utente deve vedere tutti i problemi in un colpo.

Anche i doctor check sono estensibili via entry_points (payload.doctor_checks),
esattamente come reader/writer: un plugin per un toolchain particolare può
portarsi dietro il proprio check senza toccare il core.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from payload.core.plugin_base import CheckResult, CheckStatus
from payload.core.registry import PluginRegistry


class ToolchainCheck:
    name = "toolchain"
    api_version = "1.0"

    def __init__(self, binary_key: str, binary_name: str):
        self._binary_key = binary_key  # es. "compiler" (chiave dentro [toolchain])
        self._binary_name = binary_name  # nome atteso, per il messaggio

    def run(self, config: dict) -> CheckResult:
        cmd = config.get("toolchain", {}).get(self._binary_key)
        if not cmd:
            return CheckResult(
                self.name, CheckStatus.WARN, f"'{self._binary_key}' non configurato"
            )
        path = shutil.which(cmd)
        if not path:
            return CheckResult(
                self.name,
                CheckStatus.FAIL,
                f"'{cmd}' non trovato nel PATH",
                hint=f"Installa {cmd} o aggiorna '{self._binary_key}' in table-tool.toml",
            )
        try:
            out = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5
            )
            version = (out.stdout or out.stderr or "").splitlines()[0] if out.stdout or out.stderr else "?"
            return CheckResult(self.name, CheckStatus.OK, f"{cmd} trovato: {version}")
        except subprocess.TimeoutExpired:
            return CheckResult(
                self.name, CheckStatus.WARN, f"{cmd} trovato ma non risponde a --version"
            )
        except OSError as e:
            return CheckResult(self.name, CheckStatus.FAIL, f"{cmd} non eseguibile: {e}")


class PluginLoadCheck:
    name = "plugins"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.registry import load_plugins

        project_root = Path(config.get("_project_root", "."))
        try:
            registry = load_plugins(project_root=project_root, strict=False)
        except Exception as e:  # pragma: no cover - difesa
            return CheckResult(self.name, CheckStatus.FAIL, f"Errore inatteso nel loading: {e}")

        # le dipendenze mancanti di un plugin locale (REQUIRES) sono già
        # coperte, con la severità giusta (WARN, non FAIL — non è un
        # plugin "rotto", solo una dipendenza da installare), dal check
        # 'local_plugin_deps'. Contarle due volte con severità diverse
        # sarebbe contraddittorio.
        real_failures = [f for f in registry.load_failures if f[1] != "local:deps"]

        if real_failures:
            names = ", ".join(f"{n} ({g})" for n, g, _ in real_failures)
            detail = "; ".join(f"{n}: {r}" for n, _, r in real_failures)
            return CheckResult(
                self.name,
                CheckStatus.FAIL,
                f"{len(real_failures)} plugin non caricabili: {names}",
                hint=detail,
            )

        total = len(registry.readers) + len(registry.writers) + len(registry.doctor_checks)
        return CheckResult(self.name, CheckStatus.OK, f"{total} plugin caricati correttamente")


class ConfigValidityCheck:
    """Valida table-tool.toml e scansiona tutti i sidecar *.config.toml
    del progetto, riportando quali sono malformati."""

    name = "config"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.config import load_config
        from payload.core.errors import PayloadError

        project_root = Path(config.get("_project_root", "."))
        problems = []

        try:
            load_config(project_root)
        except PayloadError as e:
            problems.append(f"table-tool.toml: {e.message}")

        sidecar_count = 0
        for sidecar in project_root.rglob("*.config.toml"):
            sidecar_count += 1
            # Il sidecar è valido solo nel contesto del proprio file sorgente,
            # ma un errore di sintassi toml lo intercettiamo comunque qui,
            # provando a caricarlo come se fosse per un sorgente omonimo.
            fake_source = sidecar.parent / sidecar.name.replace(".config.toml", ".fake")
            try:
                load_config(project_root, source_path=fake_source)
            except PayloadError as e:
                problems.append(f"{sidecar}: {e.message}")

        if problems:
            return CheckResult(
                self.name, CheckStatus.FAIL,
                f"{len(problems)} file di config non validi",
                hint="; ".join(problems),
            )
        return CheckResult(
            self.name, CheckStatus.OK,
            f"Config globale valida, {sidecar_count} sidecar controllati",
        )


class DirWritableCheck:
    name = "directories"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        dirs = [
            config.get("defaults", {}).get("output_dir", "build"),
            config.get("defaults", {}).get("golden_dir", "golden"),
            config.get("defaults", {}).get("cache_dir", ".payload_cache"),
        ]
        problems = []
        for d in dirs:
            p = Path(d)
            try:
                p.mkdir(parents=True, exist_ok=True)
                test_file = p / ".payload_write_test"
                test_file.write_text("ok")
                test_file.unlink()
            except OSError as e:
                problems.append(f"{d}: {e}")
        if problems:
            return CheckResult(
                self.name, CheckStatus.FAIL, "Directory non scrivibili: " + "; ".join(problems)
            )
        return CheckResult(self.name, CheckStatus.OK, "Tutte le directory sono scrivibili")


class CacheIntegrityCheck:
    name = "cache"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        cache_dir = Path(config.get("defaults", {}).get("cache_dir", ".payload_cache"))
        cache_file = cache_dir / ".payload_cache.json"
        if not cache_file.exists():
            return CheckResult(self.name, CheckStatus.OK, "Nessuna cache presente (ok)")
        try:
            json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            return CheckResult(
                self.name,
                CheckStatus.WARN,
                "Cache corrotta",
                hint="Cancella la cache o esegui il prossimo build con --force",
            )
        return CheckResult(self.name, CheckStatus.OK, "Cache integra")


class TableNameUniquenessCheck:
    """I nomi tabella (filename stem) sono l'identità usata per build
    output/golden/history — due sorgenti con lo stesso nome in cartelle
    diverse collidono silenziosamente. Vedi core/discovery.py."""

    name = "table_names"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.discovery import discover_table_sources, find_duplicate_stems
        from payload.core.registry import load_plugins

        project_root = Path(config.get("_project_root", "."))
        output_dir = Path(config.get("defaults", {}).get("output_dir", "build"))

        try:
            registry = load_plugins(project_root=project_root, strict=False)
        except Exception as e:  # pragma: no cover - difesa
            return CheckResult(self.name, CheckStatus.WARN, f"impossibile verificare: {e}")

        known_ext = {ext for r in registry.readers.values() for ext in r.extensions}
        sources = discover_table_sources(project_root, known_ext, output_dir)
        duplicates = find_duplicate_stems(sources)

        if duplicates:
            names = ", ".join(duplicates.keys())
            detail = "; ".join(
                f"'{name}': {', '.join(str(p) for p in paths)}" for name, paths in duplicates.items()
            )
            return CheckResult(
                self.name, CheckStatus.FAIL,
                f"{len(duplicates)} nomi tabella duplicati: {names}",
                hint=detail,
            )
        return CheckResult(self.name, CheckStatus.OK, f"{len(sources)} tabelle, nessun nome duplicato")


class LocalPluginDepsCheck:
    """Scansiona i plugin locali (local_plugins/, PAYLOAD_PLUGIN_PATH)
    e segnala quelli con REQUIRES non soddisfatte. Non bloccante (WARN):
    un plugin locale con dipendenze mancanti non impedisce a payload di
    funzionare, solo a quel plugin specifico."""

    name = "local_plugin_deps"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.local_plugins import (
            discover_local_plugin_dirs,
            missing_requirements,
            read_requires_static,
        )

        project_root = Path(config.get("_project_root", "."))
        problems = []

        for plugin_dir in discover_local_plugin_dirs(project_root):
            for py_file in sorted(plugin_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                requires = read_requires_static(py_file)
                if not requires:
                    continue
                missing = missing_requirements(requires)
                if missing:
                    problems.append(f"{py_file.name}: {', '.join(missing)}")

        if problems:
            return CheckResult(
                self.name, CheckStatus.WARN,
                f"{len(problems)} plugin locali con dipendenze mancanti",
                hint="; ".join(problems) + " — usa 'pld plugin install-deps <file>'",
            )
        return CheckResult(self.name, CheckStatus.OK, "nessuna dipendenza mancante nei plugin locali")


class GitCheck:
    """Verifica se git è disponibile. Non bloccante: payload funziona
    anche senza (la history in .payload_history/ è indipendente da
    git), ma molti workflow lo danno per scontato."""

    name = "git"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        path = shutil.which("git")
        if not path:
            return CheckResult(
                self.name, CheckStatus.WARN, "git non trovato nel PATH",
                hint="Facoltativo, ma consigliato per versionare sorgenti e config",
            )
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
            version = result.stdout.strip() or "versione sconosciuta"
        except (subprocess.TimeoutExpired, OSError):
            return CheckResult(self.name, CheckStatus.WARN, "git trovato ma non risponde a --version")

        project_root = Path(config.get("_project_root", "."))
        in_repo = (project_root / ".git").is_dir()
        repo_status = "repo git presente" if in_repo else "cartella non ancora un repo git"
        return CheckResult(self.name, CheckStatus.OK, f"{version} ({repo_status})")


def builtin_checks() -> list:
    return [
        ToolchainCheck("compiler", "compiler"),
        ToolchainCheck("objcopy", "objcopy"),
        PluginLoadCheck(),
        ConfigValidityCheck(),
        TableNameUniquenessCheck(),
        LocalPluginDepsCheck(),
        GitCheck(),
        DirWritableCheck(),
        CacheIntegrityCheck(),
    ]


def run_doctor(config: dict, registry: PluginRegistry | None = None) -> list[CheckResult]:
    checks = builtin_checks()
    if registry is not None:
        checks.extend(registry.doctor_checks.values())
    return [check.run(config) for check in checks]
