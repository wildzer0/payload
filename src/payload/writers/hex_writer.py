"""
Example writer: Intel HEX, a standard text format used to flash
firmware/data onto microcontrollers (e.g. via a programmer/bootloader).

Serves as a second, more realistic writer example than bin_writer.py,
to show a real TRANSFORMATION (not just a byte dump) starting from the
same data any other writer would receive in TableIR.data — the
destination format here has nothing to do with how the data was read
(raw_text, csv, c_source, ...): that's precisely the point of the
common IR.

Stated limit: only supports 16-bit addresses (record type 00/01, no
extended linear address 04) — enough for tables under 64KB, the common
case for embedded tables. A separate 'hex32' writer could handle
larger tables, if that's ever needed.
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
    """Standard Intel HEX format (record type 00/01), used to flash
    firmware/data onto microcontrollers via a programmer/bootloader.

    16 bytes per line, checksum computed automatically. Only supports
    16-bit addresses (tables under 64KB) — beyond that limit it raises
    WriterEmitError instead of producing a truncated or incorrect HEX
    file."""

    name = "hex"
    extension = ".hex"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        if len(ir.data) > 0xFFFF:
            raise WriterEmitError(
                self.name,
                f"table of {len(ir.data)} bytes exceeds the 16-bit limit (65535) "
                f"supported by this writer",
            )

        lines = []
        for offset in range(0, len(ir.data), BYTES_PER_LINE):
            chunk = ir.data[offset:offset + BYTES_PER_LINE]
            lines.append(_data_record(offset, chunk))
        lines.append(_eof_record())

        out_path.write_text("\n".join(lines) + "\n")
        return out_path
