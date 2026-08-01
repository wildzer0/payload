"""
Plugin locali: reader/writer/doctor-check definiti in un singolo file
.py, caricati senza passare da 'pip install'. Pensati per esperimenti
rapidi o formati specifici di un singolo progetto — per un plugin
riusabile su più progetti, un vero pacchetto pip (pld plugin new)
resta la scelta giusta (versionabile, distribuibile, installabile via
indice interno).

Due modi in cui un file viene scoperto:
1. Cartella 'local_plugins/' accanto a table-tool.toml (o comunque
   dentro project_root) — scansionata automaticamente.
2. Variabile d'ambiente PAYLOAD_PLUGIN_PATH — lista di cartelle
   aggiuntive separate da os.pathsep, cercate ovunque sul filesystem.

Convenzione nel file .py: espone le classi plugin come variabili a
livello di modulo, singolari o plurali:

    class MyReader:
        name = "my_format"
        ...

    READER = MyReader          # singolare: un plugin
    # oppure
    READERS = [MyReader, ...]  # plurale: più plugin nello stesso file

Stessa cosa per WRITER/WRITERS e DOCTOR_CHECK/DOCTOR_CHECKS.

Se il plugin ha bisogno di librerie terze non già presenti nell'ambiente
di payload, dichiaralo con REQUIRES a livello di modulo:

    REQUIRES = ["numpy>=1.20", "pyserial"]

Verificato PRIMA di tentare il caricamento del modulo (lettura statica
del sorgente, senza eseguirlo) — così anche se il modulo fallirebbe con
un ModuleNotFoundError poco chiaro, l'errore riportato dice esattamente
quale dipendenza manca. 'pld plugin install-deps <file>' le installa
con pip nell'ambiente corrente."""
from __future__ import annotations

import ast
import importlib.util
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from payload.core.errors import MissingPluginDependenciesError, PluginLoadError
from payload.core.plugin_base import check_api_compatibility

if TYPE_CHECKING:
    from payload.core.registry import PluginRegistry

logger = logging.getLogger(__name__)

LOCAL_PLUGINS_DIRNAME = "local_plugins"
ENV_VAR_NAME = "PAYLOAD_PLUGIN_PATH"

_ATTR_MAP = (
    ("READER", "READERS", "reader"),
    ("WRITER", "WRITERS", "writer"),
    ("DOCTOR_CHECK", "DOCTOR_CHECKS", "doctor_check"),
)

_REQ_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


def read_requires_static(path: Path) -> list[str]:
    """Legge REQUIRES = [...] dal sorgente SENZA eseguirlo (parsing
    AST) — funziona anche se il modulo non è importabile perché manca
    proprio la dipendenza che REQUIRES dichiarerebbe."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "REQUIRES" for t in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [
                    elt.value for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    return []


_STUB_METHOD_NAMES = {"parse", "emit", "run"}


def find_stub_methods(path: Path) -> list[str]:
    """Trova i metodi 'parse'/'emit'/'run' il cui intero corpo è ancora
    un singolo 'raise NotImplementedError(...)' — esattamente quello che
    lascia lo scaffold generato da 'pld plugin new-local' (vedi
    plugin_scaffold.py) prima di essere completato. Analisi statica
    (AST), come read_requires_static: funziona anche se il modulo non
    è (ancora) importabile."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in _STUB_METHOD_NAMES:
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # salta la docstring, se presente
        if len(body) != 1 or not isinstance(body[0], ast.Raise):
            continue
        exc = body[0].exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            exc_name = exc.func.id
        elif isinstance(exc, ast.Name):
            exc_name = exc.id
        else:
            exc_name = None
        if exc_name == "NotImplementedError":
            found.append(node.name)
    return found


def missing_requirements(requires: list[str]) -> list[str]:
    """Controllo best-effort: estrae il nome pacchetto da ogni stringa
    di requisito (ignora version specifier) e verifica che sia
    importabile. Non è una risoluzione delle dipendenze vera e propria
    (non controlla versioni) — solo 'è installato sì/no'."""
    missing = []
    for req in requires:
        match = _REQ_NAME_RE.match(req.strip())
        if not match:
            continue
        pkg_name = match.group(0).replace("-", "_")
        if importlib.util.find_spec(pkg_name) is None:
            missing.append(req)
    return missing


def discover_local_plugin_dirs(project_root: Path) -> list[Path]:
    dirs = []
    default_dir = project_root / LOCAL_PLUGINS_DIRNAME
    if default_dir.is_dir():
        dirs.append(default_dir)

    env_value = os.environ.get(ENV_VAR_NAME)
    if env_value:
        for part in env_value.split(os.pathsep):
            p = Path(part).expanduser()
            if p.is_dir():
                dirs.append(p)
            else:
                logger.debug("PAYLOAD_PLUGIN_PATH contiene una cartella inesistente: %s", p)

    return dirs


def load_module_from_file(path: Path):
    """Pubblica (non solo per load_local_plugins): usata anche
    dall'editor web per caricare ed eseguire un singolo file plugin
    locale senza passare da un intero PluginRegistry."""
    spec = importlib.util.spec_from_file_location(f"payload_local_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - difesa
        raise ImportError(f"impossibile creare uno spec per {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_plugin_classes(module) -> list[tuple[str, type]]:
    """Ritorna [(kind, classe), ...] per ogni READER/WRITER/DOCTOR_CHECK
    (singolare o plurale) dichiarato nel modulo — stessa convenzione di
    load_local_plugins, fattorizzata così l'editor web può ispezionare
    un file senza registrarlo in un PluginRegistry."""
    found: list[tuple[str, type]] = []
    for singular_attr, plural_attr, kind in _ATTR_MAP:
        single = getattr(module, singular_attr, None)
        if single is not None:
            found.append((kind, single))
        plural = getattr(module, plural_attr, None)
        if plural:
            for cls in plural:
                found.append((kind, cls))
    return found


def list_local_plugin_files(project_root: Path) -> list[Path]:
    """File .py in <project_root>/local_plugins/ (solo la cartella del
    progetto, non i percorsi aggiuntivi di PAYLOAD_PLUGIN_PATH — quelli
    possono vivere ovunque sul filesystem e non sono nel dominio di
    'cosa può modificare l'editor web di questo progetto')."""
    d = project_root / LOCAL_PLUGINS_DIRNAME
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def _register_one(plugin_cls, source_file: Path, kind: str, register_fn, registry: "PluginRegistry", strict: bool) -> None:
    plugin_label = f"{plugin_cls.__name__} ({source_file.name})"
    try:
        instance = plugin_cls()
        api_version = getattr(instance, "api_version", "0.0")
        check_api_compatibility(getattr(instance, "name", plugin_cls.__name__), api_version)
        register_fn(instance)
        logger.debug("Plugin locale caricato: %s (%s) da %s", getattr(instance, "name", "?"), kind, source_file)
    except Exception as e:
        logger.debug("Fallito plugin locale %s: %s", plugin_label, e)
        if strict:
            raise PluginLoadError(plugin_label, f"local:{kind}", str(e)) from e
        registry.load_failures.append((plugin_label, f"local:{kind}", str(e)))


def load_local_plugins(project_root: Path, registry: "PluginRegistry", strict: bool = True) -> None:
    for plugin_dir in discover_local_plugin_dirs(project_root):
        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # convenzione: file che iniziano per _ non sono plugin (es. helper condivisi)

            declared_requires = read_requires_static(py_file)
            if declared_requires:
                missing = missing_requirements(declared_requires)
                if missing:
                    logger.debug("Dipendenze mancanti per %s: %s", py_file, missing)
                    if strict:
                        raise MissingPluginDependenciesError(py_file, missing)
                    registry.load_failures.append(
                        (py_file.name, "local:deps", f"dipendenze mancanti: {', '.join(missing)}")
                    )
                    continue  # non tentare nemmeno il caricamento, fallirebbe comunque

            try:
                module = load_module_from_file(py_file)
            except Exception as e:
                logger.debug("Fallito caricamento file %s: %s", py_file, e)
                if strict:
                    raise PluginLoadError(py_file.name, "local:file", str(e)) from e
                registry.load_failures.append((py_file.name, "local:file", str(e)))
                continue

            for singular_attr, plural_attr, kind in _ATTR_MAP:
                register_fn = {
                    "reader": registry.register_reader,
                    "writer": registry.register_writer,
                    "doctor_check": registry.register_doctor_check,
                }[kind]

                single = getattr(module, singular_attr, None)
                if single is not None:
                    _register_one(single, py_file, kind, register_fn, registry, strict)

                plural = getattr(module, plural_attr, None)
                if plural:
                    for cls in plural:
                        _register_one(cls, py_file, kind, register_fn, registry, strict)
