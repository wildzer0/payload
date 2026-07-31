"""
Reader di esempio: formato CSV con colonne 'value' (obbligatoria, hex o
decimale), 'width' (opzionale, larghezza in bytes: 1/2/4/8, default 1) e
'comment' (opzionale). L'offset è implicito dall'ordine delle righe,
salvo colonna 'offset' esplicita.

Serve come secondo esempio, più realistico di raw_text.py, per mostrare:
- come un reader gestisce un formato strutturato (non solo testo libero)
- come si popolano TUTTI i campi di TableIR, comments incluso
- come si segnalano errori di parsing riga per riga
- come un reader con valori multi-byte gestisce l'endianness (vedi
  docs/PLUGINS.md, sezione "Gestire l'endianness")

    value,width,comment
    0x0A,1,soglia min
    0x1234,2,timeout in ms
    0xDEADBEEF,4,magic number

Senza colonna 'width', ogni valore è trattato come singolo byte (come
prima) — retrocompatibile con CSV scritti prima di questa estensione.
"""
from __future__ import annotations

import csv
from pathlib import Path

from payload.core.byteorder import pack_value
from payload.core.errors import ReaderParseError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

_MAX_BY_WIDTH = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF, 8: 0xFFFFFFFFFFFFFFFF}


class CsvReader:
    """CSV strutturato con colonne 'value' (obbligatoria, hex o
    decimale), 'width' (opzionale, larghezza in bytes: 1/2/4/8, default
    1) e 'comment' (opzionale). Offset implicito dall'ordine delle
    righe, salvo colonna 'offset' esplicita.

    Esempio:
        value,width,comment
        0x0A,1,soglia min
        0x1234,2,timeout in ms
        0xDEADBEEF,4,magic number

    Rispetta 'defaults.byte_order' dalla config per i valori multi-byte
    (vedi docs/PLUGINS.md, sezione "Gestire l'endianness"). Senza
    colonna 'width', ogni valore è un singolo byte (retrocompatibile)."""

    name = "csv"
    extensions = [".csv"]
    api_version = PLUGIN_API_VERSION
    default_writer = "bin"

    def sniff(self, path: Path) -> bool:
        try:
            head = path.read_text(errors="ignore").splitlines()[:1]
        except OSError:
            return False
        return bool(head) and "value" in head[0].lower()

    def parse(self, path: Path, config: dict) -> TableIR:
        byte_order = config.get("defaults", {}).get("byte_order", "little")

        data = bytearray()
        comments: list[tuple[int, str]] = []
        fields: list[dict] = []

        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "value" not in reader.fieldnames:
                raise ReaderParseError(path, "manca la colonna obbligatoria 'value'")

            for row_num, row in enumerate(reader, start=2):  # start=2: la riga 1 è l'header
                raw_value = (row.get("value") or "").strip()
                if not raw_value:
                    continue

                try:
                    value = int(raw_value, 0)  # base 0: accetta sia '0x0A' che '10'
                except ValueError as e:
                    raise ReaderParseError(
                        path, f"riga {row_num}: valore non valido '{raw_value}'"
                    ) from e

                width_field = (row.get("width") or "").strip()
                width = int(width_field) if width_field else 1
                if width not in _MAX_BY_WIDTH:
                    raise ReaderParseError(
                        path, f"riga {row_num}: larghezza non supportata '{width}' (ammessi: 1, 2, 4, 8)"
                    )
                if not 0 <= value <= _MAX_BY_WIDTH[width]:
                    raise ReaderParseError(
                        path, f"riga {row_num}: valore fuori range per {width} byte: {value}"
                    )

                offset_field = (row.get("offset") or "").strip()
                offset = int(offset_field, 0) if offset_field else len(data)

                # se l'offset esplicito lascia un buco rispetto ai dati già
                # scritti, riempiamo con zeri per mantenere data contigua
                while len(data) < offset:
                    data.append(0)
                if offset < len(data):
                    raise ReaderParseError(
                        path, f"riga {row_num}: offset {offset} sovrappone dati già scritti"
                    )

                data += pack_value(value, width, byte_order)
                fields.append({"offset": offset, "width": width, "value": value})

                comment = (row.get("comment") or "").strip()
                if comment:
                    comments.append((offset, comment))

        return TableIR(
            name=path.stem,
            data=bytes(data),
            source_path=path,
            source_format=self.name,
            byte_order=byte_order,
            comments=comments,
            extra={"fields": fields},
        )
