"""
Writer di esempio: Intel HEX, formato testuale standard usato per
flashare firmware/dati su microcontrollori (es. via programmer/bootloader).

Serve come secondo esempio di writer, più realistico di bin_writer.py, per
mostrare una vera TRASFORMAZIONE (non solo dump di bytes) a partire dagli
stessi dati che qualsiasi altro writer riceverebbe in TableIR.data — il
formato di destinazione qui non c'entra nulla con come i dati sono stati
letti (raw_text, csv, c_source, ...): è proprio il punto della IR comune.

Limite dichiarato: supporta solo indirizzi a 16 bit (record type 00/01,
niente extended linear address 04) — sufficiente per tabelle sotto i 64KB,
il caso comune per tabelle embedded. Un writer 'hex32' separato potrebbe
gestire tabelle più grandi, se mai servisse.
"""
from __future__ import annotations

from pathlib import Path

from payload.core.errors import WriterEmitError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

BYTES_PER_LINE = 16
RECORD_DATA = 0x00
RECORD_EOF = 0x01


def _checksum(byte_values: list[int]) -> int:
    return (-sum(byte_values)) & 0xFF


def _data_record(address: int, chunk: bytes) -> str:
    fields = [len(chunk), (address >> 8) & 0xFF, address & 0xFF, RECORD_DATA, *chunk]
    hex_fields = "".join(f"{b:02X}" for b in fields)
    return f":{hex_fields}{_checksum(fields):02X}"


def _eof_record() -> str:
    fields = [0, 0, 0, RECORD_EOF]
    return f":{''.join(f'{b:02X}' for b in fields)}{_checksum(fields):02X}"


class HexWriter:
    """Formato Intel HEX standard (record type 00/01), usato per
    flashare firmware/dati su microcontrollori via programmer/bootloader.

    16 bytes per riga, checksum calcolato automaticamente. Supporta solo
    indirizzi a 16 bit (tabelle sotto i 64KB) — oltre quel limite
    solleva WriterEmitError invece di produrre un file HEX troncato o
    scorretto."""

    name = "hex"
    extension = ".hex"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        if len(ir.data) > 0xFFFF:
            raise WriterEmitError(
                self.name,
                f"tabella di {len(ir.data)} bytes supera il limite a 16 bit (65535) "
                f"supportato da questo writer",
            )

        lines = []
        for offset in range(0, len(ir.data), BYTES_PER_LINE):
            chunk = ir.data[offset:offset + BYTES_PER_LINE]
            lines.append(_data_record(offset, chunk))
        lines.append(_eof_record())

        out_path.write_text("\n".join(lines) + "\n")
        return out_path
