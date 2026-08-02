"""
Example reader: CSV format with a 'value' column (required, hex or
decimal), a 'width' column (optional, width in bytes: 1/2/4/8, default
1) and a 'comment' column (optional). The offset is implicit from row
order, unless an explicit 'offset' column is given.

Serves as a second, more realistic example than raw_text.py, to show:
- how a reader handles a structured format (not just free text)
- how ALL of TableIR's fields get populated, comments included
- how parsing errors are reported line by line
- how a reader with multi-byte values handles endianness (see
  src/payload/docs/PLUGINS.md, "Handling endianness" section)

    value,width,comment
    0x0A,1,min threshold
    0x1234,2,timeout in ms
    0xDEADBEEF,4,magic number

Without a 'width' column, every value is treated as a single byte (as
before) — backward compatible with CSVs written before this extension.

Not installed by default — copy this file (or 'pld plugin install
examples/plugins/csv_reader.py') into a project's plugins/ folder to
use it, see src/payload/docs/PLUGINS.md.
"""
from __future__ import annotations

import csv
from pathlib import Path

from payload.core.byteorder import pack_value
from payload.core.errors import ReaderParseError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

_MAX_BY_WIDTH = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF, 8: 0xFFFFFFFFFFFFFFFF}


class CsvReader:
    """Structured CSV with a 'value' column (required, hex or
    decimal), a 'width' column (optional, width in bytes: 1/2/4/8,
    default 1) and a 'comment' column (optional). Offset implicit from
    row order, unless an explicit 'offset' column is given.

    Example:
        value,width,comment
        0x0A,1,min threshold
        0x1234,2,timeout in ms
        0xDEADBEEF,4,magic number

    Honors 'defaults.byte_order' from config for multi-byte values
    (see src/payload/docs/PLUGINS.md, "Handling endianness" section). Without
    a 'width' column, every value is a single byte (backward compatible)."""

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
                raise ReaderParseError(path, "missing the required 'value' column")

            for row_num, row in enumerate(reader, start=2):  # start=2: row 1 is the header
                raw_value = (row.get("value") or "").strip()
                if not raw_value:
                    continue

                try:
                    value = int(raw_value, 0)  # base 0: accepts both '0x0A' and '10'
                except ValueError as e:
                    raise ReaderParseError(
                        path, f"row {row_num}: invalid value '{raw_value}'"
                    ) from e

                width_field = (row.get("width") or "").strip()
                width = int(width_field) if width_field else 1
                if width not in _MAX_BY_WIDTH:
                    raise ReaderParseError(
                        path, f"row {row_num}: unsupported width '{width}' (allowed: 1, 2, 4, 8)"
                    )
                if not 0 <= value <= _MAX_BY_WIDTH[width]:
                    raise ReaderParseError(
                        path, f"row {row_num}: value out of range for {width} byte(s): {value}"
                    )

                offset_field = (row.get("offset") or "").strip()
                offset = int(offset_field, 0) if offset_field else len(data)

                # if the explicit offset leaves a gap relative to the data
                # already written, fill with zeros to keep data contiguous
                while len(data) < offset:
                    data.append(0)
                if offset < len(data):
                    raise ReaderParseError(
                        path, f"row {row_num}: offset {offset} overlaps data already written"
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


READER = CsvReader
