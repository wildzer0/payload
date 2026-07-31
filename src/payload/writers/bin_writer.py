"""Writer di esempio: scrive i bytes della IR.

Se la config richiede un byte_order diverso da quello con cui il reader
ha impacchettato i dati (ir.byte_order), E il reader ha esposto i valori
strutturati in ir.extra["fields"], li ripacchetta nell'ordine richiesto
prima di scrivere — è così che un reader 'little' e un writer 'big'
possono convivere. Se il reader non espone i campi (perché lavora solo
con singoli byte, dove l'ordine non ha senso, o perché è un reader più
semplice), il writer scrive i bytes così come ricevuti, senza tentare
reinterpretazioni alla cieca."""
from __future__ import annotations

import logging
from pathlib import Path

from payload.core.byteorder import repack
from payload.core.ir import TableIR

logger = logging.getLogger(__name__)


class BinWriter:
    """Scrive i bytes di TableIR.data così come sono: nessun involucro,
    nessuna intestazione, il file di output è esattamente ir.data.

    Se 'defaults.byte_order' in config richiede un ordine diverso da
    quello con cui il reader ha impacchettato i dati, e il reader espone
    ir.extra['fields'] (valori strutturati, non solo bytes), ripacchetta
    automaticamente nell'ordine richiesto. Altrimenti scrive i bytes
    invariati con un avviso (mai uno swap alla cieca)."""

    name = "bin"
    extension = ".bin"
    api_version = "1.0"

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        target_order = config.get("defaults", {}).get("byte_order", ir.byte_order)

        if target_order != ir.byte_order:
            fields = ir.extra.get("fields")
            if fields:
                logger.debug(
                    "Ripacchetto %d campi da %s a %s", len(fields), ir.byte_order, target_order
                )
                out_path.write_bytes(repack(fields, target_order))
                return out_path
            logger.warning(
                "byte_order richiesto (%s) diverso da quello del reader (%s), ma "
                "'%s' non espone campi strutturati: scrivo i bytes così come ricevuti",
                target_order, ir.byte_order, ir.source_format,
            )

        out_path.write_bytes(ir.data)
        return out_path
