"""
Config a tre livelli, precedenza crescente:
  1. table-tool.toml globale (root progetto)
  2. sidecar per-tabella (<nome>.config.toml accanto al sorgente)
  3. flag CLI

Il merge e' "deep": il sidecar sovrascrive solo le chiavi che dichiara,
non l'intera sezione.

Validazione fatta a mano con dataclasses stdlib, deliberatamente senza
pydantic: pydantic-core e' un'estensione Rust senza wheel precompilata
per diverse piattaforme ARM (es. Termux su Android), il che rompeva
l'installazione li'. dataclasses + validazione esplicita e' l'unica
dipendenza compilata in meno, e rende il tool installabile ovunque ci
sia un Python puro.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

from payload.core.errors import InvalidConfigError

logger = logging.getLogger(__name__)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

GLOBAL_CONFIG_FILENAME = "table-tool.toml"
SIDECAR_SUFFIX = ".config.toml"


@dataclass
class ToolchainConfig:
    compiler: str = "gcc"
    compiler_flags: list[str] = field(default_factory=list)
    objcopy: str = "objcopy"
    # richiesti solo dal writer 'obj' (objcopy -I binary non ha un
    # default universale): es. objcopy_target="elf32-littlearm",
    # objcopy_arch="arm" per un target ARM Cortex-M
    objcopy_target: str = ""
    objcopy_arch: str = ""


@dataclass
class DefaultsConfig:
    writer: str | None = None  # None = nessuna preferenza esplicita: il reader può suggerirne uno
    reader: str | None = None  # None = auto-risoluzione da estensione/sniff (vedi PluginRegistry.find_reader)
    output_dir: str = "build"
    cache_dir: str = ".payload_cache"
    byte_order: str = "little"  # "little" | "big" — target per reader/writer che gestiscono valori multi-byte


@dataclass
class PayloadConfig:
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    toolchain: ToolchainConfig = field(default_factory=ToolchainConfig)
    # Territorio dei plugin, non del core: [plugin.<nome>] in
    # table-tool.toml/sidecar finisce qui senza validazione di schema —
    # il core non può sapere di cosa un plugin di terze parti ha
    # bisogno. Un reader/writer legge la propria fetta con
    # config.get("plugin", {}).get(self.name, {}).
    plugin: dict = field(default_factory=dict)
    # [pipeline] stages = [...] — lista grezza di dict, validata e
    # trasformata in PipelineSpec da core/pipeline_spec.py, non qui
    # (il core config resta generico, non deve conoscere le regole di
    # alternanza reader/writer/exec). Vuota = nessuna pipeline esplicita,
    # si usa la risoluzione implicita da --from/--to (vedi core/pipeline.py).
    pipeline_stages: list = field(default_factory=list)

    def model_dump(self) -> dict:
        """Nome del metodo mantenuto uguale a pydantic v2 apposta: nessun
        altro modulo (pipeline, cli, doctor) deve sapere che sotto non
        c'e' piu' pydantic."""
        return asdict(self)


_DEFAULTS_STR_FIELDS = ("writer", "reader", "output_dir", "cache_dir", "byte_order")
_TOOLCHAIN_STR_FIELDS = ("compiler", "objcopy", "objcopy_target", "objcopy_arch")
_TOOLCHAIN_LIST_STR_FIELDS = ("compiler_flags",)
_VALID_BYTE_ORDERS = ("little", "big")


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise InvalidConfigError(path, field="<root>", reason=str(e)) from e


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_section(
    section: dict, section_name: str, str_fields: tuple, list_str_fields: tuple, path: Path
) -> None:
    known = set(str_fields) | set(list_str_fields)
    for key, value in section.items():
        field_path = f"{section_name}.{key}"
        if key not in known:
            raise InvalidConfigError(path, field=field_path, reason="campo sconosciuto")
        if key in str_fields and not isinstance(value, str):
            raise InvalidConfigError(
                path, field=field_path,
                reason=f"deve essere una stringa, trovato {type(value).__name__}",
            )
        if key in list_str_fields:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise InvalidConfigError(path, field=field_path, reason="deve essere una lista di stringhe")


def _build_config(merged: dict, path: Path) -> PayloadConfig:
    defaults_dict = merged.get("defaults", {})
    toolchain_dict = merged.get("toolchain", {})
    plugin_dict = merged.get("plugin", {})
    pipeline_dict = merged.get("pipeline", {})

    if not isinstance(defaults_dict, dict):
        raise InvalidConfigError(path, field="defaults", reason="deve essere una tabella TOML, es. [defaults]")
    if not isinstance(toolchain_dict, dict):
        raise InvalidConfigError(path, field="toolchain", reason="deve essere una tabella TOML, es. [toolchain]")
    if not isinstance(plugin_dict, dict):
        raise InvalidConfigError(path, field="plugin", reason="deve essere una tabella TOML, es. [plugin.nome_plugin]")
    if not isinstance(pipeline_dict, dict):
        raise InvalidConfigError(path, field="pipeline", reason="deve essere una tabella TOML, es. [pipeline]")

    pipeline_stages = pipeline_dict.get("stages", [])
    if not isinstance(pipeline_stages, list):
        raise InvalidConfigError(path, field="pipeline.stages", reason="deve essere una lista di stage")

    unknown_top = set(merged.keys()) - {"defaults", "toolchain", "plugin", "pipeline"}
    if unknown_top:
        raise InvalidConfigError(path, field=next(iter(unknown_top)), reason="sezione sconosciuta")

    _validate_section(defaults_dict, "defaults", _DEFAULTS_STR_FIELDS, (), path)
    _validate_section(toolchain_dict, "toolchain", _TOOLCHAIN_STR_FIELDS, _TOOLCHAIN_LIST_STR_FIELDS, path)
    # [plugin.*] è deliberatamente NON validato qui: è territorio del
    # plugin, il core non ha modo di sapere quali chiavi siano legittime.
    # [pipeline.stages] è deliberatamente validato solo strutturalmente
    # (lista sì/no): le regole di alternanza reader/writer/exec sono
    # territorio di core/pipeline_spec.py, non del core config.

    byte_order = defaults_dict.get("byte_order", "little")
    if byte_order not in _VALID_BYTE_ORDERS:
        raise InvalidConfigError(
            path, field="defaults.byte_order",
            reason=f"deve essere 'little' o 'big', trovato '{byte_order}'",
        )

    return PayloadConfig(
        defaults=DefaultsConfig(**defaults_dict),
        toolchain=ToolchainConfig(**toolchain_dict),
        plugin=plugin_dict,
        pipeline_stages=pipeline_stages,
    )


def load_config(project_root: Path, source_path: Path | None = None) -> PayloadConfig:
    """Carica il config globale, applica sopra l'eventuale sidecar della
    tabella specifica se source_path e' dato, valida il risultato."""
    config, _ = resolve_config_with_provenance(project_root, source_path)
    return config


def _flatten_keys(data: dict, prefix: str = "") -> list[str]:
    keys = []
    for k, v in data.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
        else:
            keys.append(full)
    return keys


def resolve_config_with_provenance(
    project_root: Path, source_path: Path | None = None
) -> tuple[PayloadConfig, dict[str, str]]:
    """Come load_config, ma ritorna anche da dove viene ogni valore
    ('default' | 'globale (table-tool.toml)' | 'sidecar (<file>)') —
    usato da 'pld config show' per il debug della risoluzione a 3 livelli."""
    merged: dict = {}
    provenance: dict[str, str] = {}

    global_path = project_root / GLOBAL_CONFIG_FILENAME
    if global_path.exists():
        logger.debug("Config globale trovata: %s", global_path)
        global_data = _load_toml(global_path)
        merged = global_data
        for key in _flatten_keys(global_data):
            provenance[key] = f"globale ({GLOBAL_CONFIG_FILENAME})"
    else:
        logger.debug("Nessuna config globale in %s, uso i default", global_path)

    if source_path is not None:
        sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
        if sidecar_path.exists():
            sidecar_data = _load_toml(sidecar_path)
            logger.debug(
                "Sidecar applicato per %s: %s (chiavi: %s)",
                source_path.name, sidecar_path.name, list(sidecar_data.keys()),
            )
            merged = _deep_merge(merged, sidecar_data)
            for key in _flatten_keys(sidecar_data):
                provenance[key] = f"sidecar ({sidecar_path.name})"

    config = _build_config(merged, global_path)

    # ogni campo non toccato da global/sidecar viene dal default della dataclass
    for section_name, section_obj in (("defaults", config.defaults), ("toolchain", config.toolchain)):
        for f in fields(section_obj):
            key = f"{section_name}.{f.name}"
            provenance.setdefault(key, "default")

    logger.debug(
        "Config risolta per %s: writer=%s toolchain=%s",
        source_path.name if source_path else "<globale>",
        config.defaults.writer, config.toolchain.compiler,
    )
    return config, provenance


def config_schema() -> dict:
    """Schema dei campi editabili di 'defaults'/'toolchain', nell'ordine
    di dichiarazione delle dataclass — usato dalla web UI per generare il
    form di config senza duplicare l'elenco dei campi lato JS."""
    def _section(cls, list_fields: tuple) -> list[dict]:
        return [{"key": f.name, "type": "list" if f.name in list_fields else "string"} for f in fields(cls)]

    return {
        "defaults": _section(DefaultsConfig, ()),
        "toolchain": _section(ToolchainConfig, _TOOLCHAIN_LIST_STR_FIELDS),
    }


def _drop_none_values(section: dict) -> dict:
    """'defaults.writer' e' l'unico campo con default None ('nessuna
    preferenza esplicita') — un form che lo invia vuoto manda None, che
    va trattato come "chiave assente", non come valore stringa
    invalido, altrimenti _validate_section lo rifiuterebbe (si aspetta
    solo str per i campi *_STR_FIELDS)."""
    return {k: v for k, v in section.items() if v is not None}


def write_global_config(project_root: Path, defaults: dict, toolchain: dict) -> Path:
    """Scrive/sovrascrive le sezioni [defaults]/[toolchain] di
    table-tool.toml, preservando [plugin.*]/[pipeline] esistenti se
    presenti. Valida PRIMA di scrivere: una config che non supererebbe
    load_config() non finisce mai su disco."""
    path = project_root / GLOBAL_CONFIG_FILENAME
    merged = dict(_load_toml(path)) if path.exists() else {}
    merged["defaults"] = _drop_none_values(defaults)
    merged["toolchain"] = _drop_none_values(toolchain)

    _build_config(merged, path)

    path.write_text(tomli_w.dumps(merged))
    logger.debug("Config globale scritta: %s", path)
    return path


def read_raw_sidecar(source_path: Path) -> dict:
    """TOML grezzo del sidecar di source_path, o {} se non esiste —
    usato dalla web UI per precompilare il form coi soli campi
    effettivamente overridati (non con l'intera config risolta)."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    if not sidecar_path.exists():
        return {}
    return _load_toml(sidecar_path)


def write_sidecar_config(
    source_path: Path,
    defaults: dict | None = None,
    toolchain: dict | None = None,
    pipeline_stages: list | None = None,
) -> Path:
    """Aggiorna il sidecar di source_path un pezzo alla volta: ogni
    parametro None lascia la sezione esistente intatta, un dict/lista
    vuoti la rimuovono (disattiva l'override senza toccare il resto del
    sidecar, es. [plugin.*] scritto a mano). Se il sidecar risultante è
    completamente vuoto, il file viene eliminato invece di lasciare un
    TOML vuoto sul disco."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    merged = dict(_load_toml(sidecar_path)) if sidecar_path.exists() else {}

    if defaults is not None:
        defaults = _drop_none_values(defaults)
        if defaults:
            merged["defaults"] = defaults
        else:
            merged.pop("defaults", None)
    if toolchain is not None:
        toolchain = _drop_none_values(toolchain)
        if toolchain:
            merged["toolchain"] = toolchain
        else:
            merged.pop("toolchain", None)
    if pipeline_stages is not None:
        if pipeline_stages:
            merged["pipeline"] = {**merged.get("pipeline", {}), "stages": pipeline_stages}
        else:
            merged.pop("pipeline", None)

    # Stessa validazione strutturale di _build_config per defaults/toolchain
    # (pipeline.stages è già stato validato dal chiamante con
    # PipelineSpec.from_raw_stages prima di arrivare qui — le regole di
    # alternanza reader/writer/exec non sono territorio di questo modulo).
    _validate_section(merged.get("defaults", {}), "defaults", _DEFAULTS_STR_FIELDS, (), sidecar_path)
    _validate_section(merged.get("toolchain", {}), "toolchain", _TOOLCHAIN_STR_FIELDS, _TOOLCHAIN_LIST_STR_FIELDS, sidecar_path)
    byte_order = merged.get("defaults", {}).get("byte_order")
    if byte_order is not None and byte_order not in _VALID_BYTE_ORDERS:
        raise InvalidConfigError(
            sidecar_path, field="defaults.byte_order",
            reason=f"deve essere 'little' o 'big', trovato '{byte_order}'",
        )

    if not merged:
        if sidecar_path.exists():
            sidecar_path.unlink()
        logger.debug("Sidecar rimosso (vuoto dopo l'update): %s", sidecar_path)
        return sidecar_path

    sidecar_path.write_text(tomli_w.dumps(merged))
    logger.debug("Sidecar scritto: %s", sidecar_path)
    return sidecar_path


def delete_sidecar_config(source_path: Path) -> bool:
    """Elimina il sidecar di source_path se esiste. Ritorna True se un
    file è stato effettivamente rimosso (idempotente: False se non
    c'era già nulla da eliminare)."""
    sidecar_path = source_path.parent / f"{source_path.stem}{SIDECAR_SUFFIX}"
    if sidecar_path.exists():
        sidecar_path.unlink()
        return True
    return False
