"""Example writer: writes the IR's bytes.

If the config requires a byte_order different from the one the reader
packed the data with (ir.byte_order), AND the reader exposed the
structured values in ir.extra["fields"], it repacks them in the
requested order before writing — this is how a 'little' reader and a
'big' writer can coexist. If the reader doesn't expose the fields
(because it only works with single bytes, where order doesn't matter,
or because it's a simpler reader), the writer writes the bytes as
received, without attempting a blind reinterpretation."""
from __future__ import annotations

import logging
from pathlib import Path

from payload.core.byteorder import repack
from payload.core.ir import TableIR

logger = logging.getLogger(__name__)


class BinWriter:
    """Writes TableIR.data's bytes as-is: no wrapper, no header, the
    output file is exactly ir.data.

    If 'defaults.byte_order' in config requires an order different
    from the one the reader packed the data with, and the reader
    exposes ir.extra['fields'] (structured values, not just bytes), it
    automatically repacks in the requested order. Otherwise it writes
    the bytes unchanged with a warning (never a blind swap)."""

    name = "bin"
    extension = ".bin"
    api_version = "1.0"

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        target_order = config.get("defaults", {}).get("byte_order", ir.byte_order)

        if target_order != ir.byte_order:
            fields = ir.extra.get("fields")
            if fields:
                logger.debug(
                    "Repacking %d fields from %s to %s", len(fields), ir.byte_order, target_order
                )
                out_path.write_bytes(repack(fields, target_order))
                return out_path
            logger.warning(
                "Requested byte_order (%s) differs from the reader's (%s), but "
                "'%s' doesn't expose structured fields: writing the bytes as received",
                target_order, ir.byte_order, ir.source_format,
            )

        out_path.write_bytes(ir.data)
        return out_path
