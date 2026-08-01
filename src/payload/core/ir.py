"""
TableIR: rappresentazione intermedia comune tra reader e writer.

Deliberatamente minimale (niente section/item/persistence per ora, vedi
`extra` come valvola di sfogo per estensioni future di dominio).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# Versione del "contratto" reader/writer/doctor-check. Un plugin dichiara
# la versione a cui è stato scritto; il core rifiuta plugin incompatibili
# con un errore esplicito invece di un crash oscuro a runtime.
PLUGIN_API_VERSION = "1.0"


@dataclass
class TableIR:
    name: str                      # nome tabella (da filename o dichiarato nel sorgente)
    data: bytes                    # payload raw già "impacchettato"
    source_path: Path              # file di origine, per cache/debug/errori
    source_format: str             # nome del reader che l'ha prodotta

    # ordine in cui i campi multi-byte SONO GIA' impacchettati in `data`.
    # Informativo se il reader non ha campi multi-byte (valore ignorato).
    byte_order: str = "little"

    # (offset, testo) — usati solo per la view, non influenzano l'output binario
    comments: list[tuple[int, str]] = field(default_factory=list)

    # spazio libero per estensioni future. Convenzione per l'endianness:
    # extra["fields"] = [{"offset": int, "width": int, "value": int}, ...]
    # opzionale — un reader lo popola solo se lavora con valori multi-byte
    # e vuole permettere a un writer di ripacchettarli in un ordine diverso
    # da byte_order (vedi payload.core.byteorder.repack). Vedi src/payload/docs/PLUGINS.md.
    extra: dict = field(default_factory=dict)
