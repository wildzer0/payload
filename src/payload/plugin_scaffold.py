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
