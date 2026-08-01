"""
Implementazione di 'pld plugin new <name>': genera lo scaffold di un
pacchetto pip installabile, con entry_point già configurato, pronto per
essere completato e installato con `pip install -e .`.
"""
from __future__ import annotations

import importlib.resources
import shutil
from pathlib import Path

GROUP_BY_KIND = {
    "reader": "readers",
    "writer": "writers",
    "doctor-check": "doctor_checks",
}


def _to_class_name(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.replace("-", "_").split("_"))


def scaffold_plugin(name: str, kind: str, dest_dir: Path) -> Path:
    """name: nome pacchetto pip, es. 'payload-reader-csv'
    kind: 'reader' | 'writer' | 'doctor-check'
    Ritorna il path della cartella creata."""
    if kind not in GROUP_BY_KIND:
        raise ValueError(f"kind sconosciuto: {kind} (atteso: {', '.join(GROUP_BY_KIND)})")

    slug = name.replace("payload-reader-", "").replace("payload-writer-", "").replace("-", "_")
    class_name = _to_class_name(slug) + (
        "Reader" if kind == "reader" else "Writer" if kind == "writer" else "Check"
    )
    package_name = name.replace("-", "_")

    template_root = importlib.resources.files("payload.templates.plugin_scaffold") / "{{plugin_name}}"
    out_root = dest_dir / name

    replacements = {
        "{{plugin_name}}": package_name,
        "{{plugin_kind}}": kind,
        "{{plugin_group}}": GROUP_BY_KIND[kind],
        "{{plugin_slug}}": slug,
        "{{plugin_class}}": class_name,
    }

    _copy_and_render(template_root, out_root, replacements)
    return out_root


_LOCAL_KINDS = ("reader", "writer", "doctor-check")


def scaffold_local_plugin(name: str, kind: str, dest_dir: Path) -> Path:
    """Genera un plugin LOCALE: un singolo file .py dentro dest_dir,
    senza pip install (a differenza di scaffold_plugin sopra, che
    genera un pacchetto pip installabile) — condivisa da 'pld plugin
    new-local' e dalla route web equivalente, un solo posto dove
    vivono i tre template inline.

    name: nome del plugin (slug), es. 'simple_reader'
    kind: 'reader' | 'writer' | 'doctor-check'
    Ritorna il path del file .py creato."""
    if kind not in _LOCAL_KINDS:
        raise ValueError(f"kind sconosciuto: '{kind}' (atteso: reader|writer|doctor-check)")

    slug = name.replace("-", "_")
    class_suffix = {"reader": "Reader", "writer": "Writer", "doctor-check": "Check"}[kind]
    raw_class_name = "".join(p.capitalize() for p in slug.split("_"))
    class_name = raw_class_name if raw_class_name.lower().endswith(class_suffix.lower()) else raw_class_name + class_suffix
    attr_name = {"reader": "READER", "writer": "WRITER", "doctor-check": "DOCTOR_CHECK"}[kind]

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{slug}.py"
    if out_path.exists():
        raise FileExistsError(f"'{out_path}' esiste già")

    if kind == "reader":
        body = f'''"""TODO: descrivi qui il formato che questo plugin legge, con un esempio."""
from __future__ import annotations

from pathlib import Path

from payload.core.errors import ReaderParseError
from payload.core.ir import TableIR


class {class_name}:
    name = "{slug}"
    extensions = [".{slug}"]
    api_version = "1.0"
    default_writer = "bin"  # opzionale: writer suggerito, o None

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        raise NotImplementedError("TODO: implementa il parsing")


{attr_name} = {class_name}
'''
    elif kind == "writer":
        body = f'''"""TODO: descrivi qui il formato che questo plugin scrive, con un esempio."""
from __future__ import annotations

from pathlib import Path

from payload.core.ir import TableIR


class {class_name}:
    name = "{slug}"
    extension = ".{slug}"
    api_version = "1.0"
    compatible_readers = None  # opzionale: lista di reader compatibili, o None per tutti

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        raise NotImplementedError("TODO: implementa la scrittura")


{attr_name} = {class_name}
'''
    else:
        body = f'''"""TODO: descrivi qui cosa verifica questo check."""
from __future__ import annotations

from payload.core.plugin_base import CheckResult, CheckStatus


class {class_name}:
    name = "{slug}"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        raise NotImplementedError("TODO: implementa il check")


{attr_name} = {class_name}
'''

    out_path.write_text(body)
    return out_path


def _copy_and_render(src: Path, dst: Path, replacements: dict[str, str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        name = item.name
        for token, value in replacements.items():
            name = name.replace(token, value)
        target = dst / name
        if item.is_dir():
            _copy_and_render(item, target, replacements)
        else:
            content = item.read_text()
            for token, value in replacements.items():
                content = content.replace(token, value)
            target.write_text(content)
