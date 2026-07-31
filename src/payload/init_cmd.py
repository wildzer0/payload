"""Implementazione di 'pld init': crea lo scaffold minimo di un progetto."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

from payload.core.config import GLOBAL_CONFIG_FILENAME
from payload.core.local_plugins import LOCAL_PLUGINS_DIRNAME

LOCAL_PLUGINS_README = '''# Plugin locali

File .py messi qui vengono scoperti automaticamente da payload, senza
bisogno di `pip install` — vedi docs/PLUGINS.md, sezione
"Plugin locali senza pip install", per la guida completa.

Convenzione minima:

    class MioWriter:
        name = "mio_writer"
        extension = ".mio"
        api_version = "1.0"

        def emit(self, ir, out_path, config):
            out_path.write_bytes(ir.data)
            return out_path

    WRITER = MioWriter

Se il plugin ha bisogno di librerie terze non gia' installate,
dichiaralo con REQUIRES a livello di modulo:

    REQUIRES = ["numpy>=1.20"]

`pld plugin install-deps <file>` le installa con pip.
'''

# Sentinel per distinguere "il chiamante non ha specificato nulla"
# (init_project() senza argomenti -> usa il template statico, con
# 'writer = "bin"' esplicito, pensato per dare un default funzionante
# subito) da "il chiamante ha scelto ESPLICITAMENTE nessuna preferenza"
# (es. il wizard, con l'utente che lascia il prompt vuoto -> writer=None
# voluto, non deve tornare 'bin' a sua insaputa). None da solo non basta
# a distinguere i due casi, quindi serve un sentinel diverso da None.
_UNSET = object()


def is_nonempty_existing_dir(path: Path) -> bool:
    return path.exists() and path.is_dir() and any(path.iterdir())


def _render_toml(writer: str | None, byte_order: str) -> str:
    lines = ["[defaults]"]
    if writer:
        lines.append(f'writer = "{writer}"')
    lines.append(f'byte_order = "{byte_order}"')
    lines.append("")
    lines.append("[toolchain]")
    lines.append('compiler = "gcc"')
    lines.append("compiler_flags = []")
    lines.append('objcopy = "objcopy"')
    lines.append("# Richiesti solo se usi il writer 'obj' (compila .c -> .o linkabile).")
    lines.append('# objcopy_target = "elf32-littlearm"')
    lines.append('# objcopy_arch = "arm"')
    return "\n".join(lines) + "\n"


def init_project(
    target_dir: Path,
    force: bool = False,
    include_local_plugins: bool = True,
    include_example: bool = True,
    writer=_UNSET,
    byte_order: str = "little",
) -> list[Path]:
    """Crea target_dir se non esiste, poi table-tool.toml, build/, golden/,
    (opzionalmente) local_plugins/ e una tabella di esempio al suo
    interno. Ritorna la lista dei file/dir creati. Non sovrascrive
    nulla salvo force=True.

    writer: se omesso (default), usa il template statico con
    'writer = "bin"' esplicito — comportamento storico, pensato per far
    funzionare subito 'pld build example_table.raw' senza flag. Passa
    esplicitamente None (es. dal wizard, quando l'utente non esprime
    una preferenza) per NON avere quel default e lasciare la
    risoluzione al meccanismo reader.default_writer/--to.

    NOTA sulla sicurezza: questa funzione non decide MAI se sia sicuro
    scrivere in target_dir (es. se è la cwd e contiene già altra roba) —
    quella decisione spetta al chiamante (cli.py), che chiede conferma
    esplicita prima di invocare questa funzione. Qui ci si fida che il
    permesso sia già stato dato."""
    created: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    templates = importlib.resources.files("payload.templates.init")

    config_dest = target_dir / GLOBAL_CONFIG_FILENAME
    if not config_dest.exists() or force:
        if writer is _UNSET:
            config_content = (templates / "table-tool.toml").read_text()
        else:
            config_content = _render_toml(writer, byte_order)
        config_dest.write_text(config_content)
        created.append(config_dest)

    for d in ("build", "golden"):
        p = target_dir / d
        p.mkdir(parents=True, exist_ok=True)
        created.append(p)

    if include_local_plugins:
        local_plugins_dir = target_dir / LOCAL_PLUGINS_DIRNAME
        local_plugins_dir.mkdir(parents=True, exist_ok=True)
        created.append(local_plugins_dir)
        readme_dest = local_plugins_dir / "README.md"
        if not readme_dest.exists() or force:
            readme_dest.write_text(LOCAL_PLUGINS_README)
            created.append(readme_dest)

    if include_example:
        example_dest = target_dir / "example_table.raw"
        if not example_dest.exists() or force:
            example_dest.write_text((templates / "example_table.raw").read_text())
            created.append(example_dest)

    return created
