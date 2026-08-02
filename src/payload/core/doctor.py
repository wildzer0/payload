"""
Builtin checks for 'pld doctor'. Each check is independent and doesn't
block the others if it fails — the user should see every problem at once.

Doctor checks are also extensible via entry_points (payload.doctor_checks),
exactly like reader/writer: a plugin for a particular toolchain can
bring its own check without touching the core.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from payload.core.plugin_base import CheckResult, CheckStatus
from payload.core.registry import PluginRegistry


class ToolchainCheck:
    """Generic "is this binary on PATH" doctor check — not toolchain-
    specific in implementation, just historically named after its
    first two uses (compiler/objcopy). A plugin that needs an external
    binary (e.g. a compiler) can register its own DOCTOR_CHECK built on
    this, reading its config from [plugin.<plugin_name>] (see
    examples/plugins/c_source.py, examples/plugins/obj_writer.py)."""

    name = "toolchain"
    api_version = "1.0"

    def __init__(self, binary_key: str, binary_name: str, plugin_name: str):
        self._binary_key = binary_key  # e.g. "compiler" (key inside [plugin.<plugin_name>])
        self._binary_name = binary_name  # expected name, for the message
        self._plugin_name = plugin_name  # e.g. "c_source" (section under [plugin.*])

    def run(self, config: dict) -> CheckResult:
        cmd = config.get("plugin", {}).get(self._plugin_name, {}).get(self._binary_key)
        if not cmd:
            return CheckResult(
                self.name, CheckStatus.WARN, f"'{self._binary_key}' not configured"
            )
        path = shutil.which(cmd)
        if not path:
            return CheckResult(
                self.name,
                CheckStatus.FAIL,
                f"'{cmd}' not found in PATH",
                hint=f"Install {cmd} or update '{self._binary_key}' under [plugin.{self._plugin_name}] in table-tool.toml",
            )
        try:
            out = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5
            )
            version = (out.stdout or out.stderr or "").splitlines()[0] if out.stdout or out.stderr else "?"
            return CheckResult(self.name, CheckStatus.OK, f"{cmd} found: {version}")
        except subprocess.TimeoutExpired:
            return CheckResult(
                self.name, CheckStatus.WARN, f"{cmd} found but not responding to --version"
            )
        except OSError as e:
            return CheckResult(self.name, CheckStatus.FAIL, f"{cmd} not executable: {e}")


class PluginLoadCheck:
    name = "plugins"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.registry import load_plugins

        project_root = Path(config.get("_project_root", "."))
        try:
            registry = load_plugins(project_root=project_root, strict=False)
        except Exception as e:  # pragma: no cover - defensive
            return CheckResult(self.name, CheckStatus.FAIL, f"Unexpected error while loading: {e}")

        # a local plugin's missing dependencies (REQUIRES) are already
        # covered, with the right severity (WARN, not FAIL — it's not a
        # "broken" plugin, just a dependency to install), by the
        # 'local_plugin_deps' check. Counting them twice with different
        # severities would be contradictory.
        real_failures = [f for f in registry.load_failures if f[1] != "local:deps"]

        if real_failures:
            names = ", ".join(f"{n} ({g})" for n, g, _ in real_failures)
            detail = "; ".join(f"{n}: {r}" for n, _, r in real_failures)
            return CheckResult(
                self.name,
                CheckStatus.FAIL,
                f"{len(real_failures)} plugins couldn't be loaded: {names}",
                hint=detail,
            )

        total = len(registry.readers) + len(registry.writers) + len(registry.doctor_checks)
        return CheckResult(self.name, CheckStatus.OK, f"{total} plugins loaded correctly")


class ConfigValidityCheck:
    """Validates table-tool.toml and scans every *.config.toml sidecar
    in the project, reporting which ones are malformed."""

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
            # A sidecar is only valid in the context of its own source
            # file, but we still catch a toml syntax error here, by
            # trying to load it as if it were for a same-named source.
            fake_source = sidecar.parent / sidecar.name.replace(".config.toml", ".fake")
            try:
                load_config(project_root, source_path=fake_source)
            except PayloadError as e:
                problems.append(f"{sidecar}: {e.message}")

        if problems:
            return CheckResult(
                self.name, CheckStatus.FAIL,
                f"{len(problems)} invalid config files",
                hint="; ".join(problems),
            )
        return CheckResult(
            self.name, CheckStatus.OK,
            f"Global config valid, {sidecar_count} sidecars checked",
        )


class DirWritableCheck:
    name = "directories"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        # Like every other check here: resolved against _project_root,
        # NEVER against the process's cwd — the two can differ (e.g.
        # 'pld serve' launched from a folder different from the served
        # project). The CLI always makes them coincide by convention,
        # but this check can't assume that.
        project_root = Path(config.get("_project_root", "."))
        dirs = [
            config.get("defaults", {}).get("output_dir", "build"),
            config.get("defaults", {}).get("cache_dir", ".payload_cache"),
        ]
        problems = []
        for d in dirs:
            p = project_root / d
            try:
                p.mkdir(parents=True, exist_ok=True)
                test_file = p / ".payload_write_test"
                test_file.write_text("ok")
                test_file.unlink()
            except OSError as e:
                problems.append(f"{d}: {e}")
        if problems:
            return CheckResult(
                self.name, CheckStatus.FAIL, "Non-writable directories: " + "; ".join(problems)
            )
        return CheckResult(self.name, CheckStatus.OK, "Every directory is writable")


class CacheIntegrityCheck:
    name = "cache"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        project_root = Path(config.get("_project_root", "."))
        cache_dir = project_root / config.get("defaults", {}).get("cache_dir", ".payload_cache")
        cache_file = cache_dir / ".payload_cache.json"
        if not cache_file.exists():
            return CheckResult(self.name, CheckStatus.OK, "No cache present (ok)")
        try:
            json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            return CheckResult(
                self.name,
                CheckStatus.WARN,
                "Corrupted cache",
                hint="Delete the cache or run the next build with --force",
            )
        return CheckResult(self.name, CheckStatus.OK, "Cache intact")


class TableNameUniquenessCheck:
    """Table names (filename stem) are the identity used for build
    output/golden/history — two sources with the same name in
    different folders silently collide. See core/discovery.py."""

    name = "table_names"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.discovery import discover_table_sources, find_duplicate_stems

        project_root = Path(config.get("_project_root", "."))
        output_dir = Path(config.get("defaults", {}).get("output_dir", "build"))
        cache_dir = Path(config.get("defaults", {}).get("cache_dir", ".payload_cache"))

        sources = discover_table_sources(project_root, output_dir, cache_dir)
        duplicates = find_duplicate_stems(sources)

        if duplicates:
            names = ", ".join(duplicates.keys())
            detail = "; ".join(
                f"'{name}': {', '.join(str(p) for p in paths)}" for name, paths in duplicates.items()
            )
            return CheckResult(
                self.name, CheckStatus.FAIL,
                f"{len(duplicates)} duplicate table names: {names}",
                hint=detail,
            )
        return CheckResult(self.name, CheckStatus.OK, f"{len(sources)} tables, no duplicate name")


class LocalPluginDepsCheck:
    """Scans project plugins (plugins/, PAYLOAD_PLUGIN_PATH) and
    reports the ones with unmet REQUIRES. Non-blocking (WARN): a local
    plugin with missing dependencies doesn't prevent payload from
    working, only that specific plugin."""

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
                f"{len(problems)} local plugins with missing dependencies",
                hint="; ".join(problems) + " — use 'pld plugin install-deps <file>'",
            )
        return CheckResult(self.name, CheckStatus.OK, "no missing dependency among local plugins")


class LocalPluginStubCheck:
    """Reports local plugins that are still incomplete scaffolds — the
    body of parse/emit/run is still 'raise NotImplementedError(...)',
    what 'pld plugin new-local' generates before it's actually
    written. Non-blocking (WARN): the plugin simply doesn't work yet,
    it's not a payload bug — but without this check the only way to
    find out was to have it blow up during a real build (see
    ReaderParseError/WriterEmitError in core/pipeline.py)."""

    name = "local_plugin_stubs"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.local_plugins import discover_local_plugin_dirs, find_stub_methods

        project_root = Path(config.get("_project_root", "."))
        problems = []

        for plugin_dir in discover_local_plugin_dirs(project_root):
            for py_file in sorted(plugin_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                stub_methods = find_stub_methods(py_file)
                if stub_methods:
                    problems.append(f"{py_file.name} ({', '.join(stub_methods)})")

        if problems:
            return CheckResult(
                self.name, CheckStatus.WARN,
                f"{len(problems)} local plugins still to implement: {', '.join(problems)}",
                hint="Complete the indicated method(s), or delete the file if it's no longer needed — see PLUGINS.md, DoctorCheck section",
            )
        return CheckResult(self.name, CheckStatus.OK, "no incomplete scaffold among local plugins")


class GitCheck:
    """Checks whether git is available. Non-blocking: payload works
    fine without it too (history in .payload_history/ is independent
    of git), but many workflows take it for granted."""

    name = "git"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        path = shutil.which("git")
        if not path:
            return CheckResult(
                self.name, CheckStatus.WARN, "git not found in PATH",
                hint="Optional, but recommended to version sources and config",
            )
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
            version = result.stdout.strip() or "unknown version"
        except (subprocess.TimeoutExpired, OSError):
            return CheckResult(self.name, CheckStatus.WARN, "git found but not responding to --version")

        project_root = Path(config.get("_project_root", "."))
        in_repo = (project_root / ".git").is_dir()
        repo_status = "git repo present" if in_repo else "folder not a git repo yet"
        return CheckResult(self.name, CheckStatus.OK, f"{version} ({repo_status})")


class PipelineExecStagesCheck:
    """Reports how many 'exec' stages are configured in the project —
    an exec stage runs arbitrary code read from config (see
    src/payload/docs/PIPELINE.md, Security section). Non-blocking: just
    visibility, so it doesn't go unnoticed."""

    name = "pipeline_exec"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        from payload.core.config import GLOBAL_CONFIG_FILENAME, _load_toml

        project_root = Path(config.get("_project_root", "."))

        toml_files = []
        global_path = project_root / GLOBAL_CONFIG_FILENAME
        if global_path.exists():
            toml_files.append(global_path)
        toml_files.extend(project_root.rglob("*.config.toml"))

        exec_count = 0
        files_with_exec = []

        for f in toml_files:
            try:
                data = _load_toml(f)
            except Exception:
                continue  # malformed file: already reported by ConfigValidityCheck, don't duplicate here
            stages = data.get("pipeline", {}).get("stages", [])
            if not isinstance(stages, list):
                continue
            n = sum(1 for s in stages if isinstance(s, dict) and s.get("type") == "exec")
            if n:
                exec_count += n
                files_with_exec.append(f.name)

        if exec_count:
            return CheckResult(
                self.name, CheckStatus.WARN,
                f"{exec_count} 'exec' stages configured in {len(files_with_exec)} files — "
                f"they run external commands during the build",
                hint=f"Files: {', '.join(files_with_exec)}. See src/payload/docs/PIPELINE.md, Security section",
            )
        return CheckResult(self.name, CheckStatus.OK, "no 'exec' stage configured")


def builtin_checks() -> list:
    return [
        PluginLoadCheck(),
        ConfigValidityCheck(),
        TableNameUniquenessCheck(),
        LocalPluginDepsCheck(),
        LocalPluginStubCheck(),
        GitCheck(),
        PipelineExecStagesCheck(),
        DirWritableCheck(),
        CacheIntegrityCheck(),
    ]


def run_doctor(config: dict, registry: PluginRegistry | None = None) -> list[CheckResult]:
    checks = builtin_checks()
    if registry is not None:
        checks.extend(registry.doctor_checks.values())

    results = []
    for check in checks:
        try:
            results.append(check.run(config))
        except Exception as e:
            # A third-party check (builtin_checks never raises
            # anything raw) might be a local scaffold still a TODO — a
            # check that blows up must not blow up the whole 'pld
            # doctor'/'GET /api/doctor', otherwise the user loses
            # visibility into ALL the other checks just because of one.
            results.append(CheckResult(
                getattr(check, "name", type(check).__name__),
                CheckStatus.FAIL,
                f"the check raised an unexpected error ({type(e).__name__}): {e}",
                hint="If this is a freshly created local plugin, it might still be an incomplete scaffold — see PLUGINS.md, DoctorCheck section",
            ))
    return results
