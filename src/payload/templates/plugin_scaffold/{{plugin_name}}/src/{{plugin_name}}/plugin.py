"""Stub generato da 'pld plugin new'. Implementa i metodi mancanti.

TODO: sostituisci QUESTA docstring con una spiegazione vera del formato
che questo plugin gestisce. Non e' un dettaglio stilistico: e' quello
che 'pld plugin info {{plugin_slug}}' mostra a chiunque installi il tuo
plugin senza aver mai letto il tuo codice. Includi almeno:
- che aspetto ha il file di input/output (un esempio concreto aiuta)
- eventuali colonne/campi obbligatori vs opzionali
- limiti noti (dimensione massima, valori ammessi, ecc.)
"""
from __future__ import annotations

from pathlib import Path

from payload.core.ir import PLUGIN_API_VERSION, TableIR


class {{plugin_class}}:
    name = "{{plugin_slug}}"
    api_version = PLUGIN_API_VERSION

    # --- solo per reader: rimuovi se stai scrivendo un writer ---
    extensions = [".{{plugin_slug}}"]
    # Opzionale: writer suggerito quando l'utente non specifica --to.
    # Rimuovi la riga (o lascia None) se non c'e' un default ovvio.
    default_writer = None

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        raise NotImplementedError("TODO: implementa il parsing")

    # --- solo per writer: rimuovi se stai scrivendo un reader ---
    extension = ".{{plugin_slug}}"
    # Opzionale: se il tuo writer ha senso SOLO con specifici reader
    # (perche' richiede semantica particolare in ir.extra), elencali qui.
    # None = compatibile con qualsiasi reader (comportamento di default).
    compatible_readers = None

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        raise NotImplementedError("TODO: implementa la scrittura")
