"""
TableIR: common intermediate representation shared between readers and
writers.

Deliberately minimal (no section/item/persistence for now, see `extra`
as an escape hatch for future domain-specific extensions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# Version of the reader/writer/doctor-check "contract". A plugin
# declares the version it was written against; the core rejects
# incompatible plugins with an explicit error instead of an obscure
# crash at runtime.
PLUGIN_API_VERSION = "1.0"


@dataclass
class TableIR:
    name: str                      # table name (from filename or declared in the source)
    data: bytes                    # already "packed" raw payload
    source_path: Path              # origin file, for cache/debug/errors
    source_format: str             # name of the reader that produced it

    # order in which multi-byte fields ARE ALREADY packed in `data`.
    # Informational if the reader has no multi-byte fields (value ignored).
    byte_order: str = "little"

    # (offset, text) — used only for the view, don't affect the binary output
    comments: list[tuple[int, str]] = field(default_factory=list)

    # free space for future extensions. Convention for endianness:
    # extra["fields"] = [{"offset": int, "width": int, "value": int}, ...]
    # optional — a reader populates it only if it works with multi-byte
    # values and wants to let a writer repack them in an order different
    # from byte_order (see payload.core.byteorder.repack). See src/payload/docs/PLUGINS.md.
    extra: dict = field(default_factory=dict)
