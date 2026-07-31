"""
Helper per gestire l'endianness in modo esplicito.

Il problema: TableIR.data sono bytes già impacchettati — nessuna
informazione su dove iniziano/finiscono i campi multi-byte. Un writer
che fa solo write_bytes(ir.data) non può reinterpretare l'ordine perché
è cieco rispetto ai confini dei campi.

La soluzione: un reader che lavora con valori multi-byte può (non deve)
popolare TableIR.extra["fields"] con i valori STRUTTURATI, non solo i
bytes già impacchettati:

    ir.extra["fields"] = [
        {"offset": 0, "width": 2, "value": 0x1234},
        {"offset": 2, "width": 4, "value": 0xDEADBEEF},
    ]

Un writer che vuole un ordine diverso da ir.byte_order può chiamare
repack() su questa lista per ottenere bytes nel proprio ordine target,
senza dover reinterpretare byte grezzi alla cieca.
"""
from __future__ import annotations

import struct

VALID_ORDERS = ("little", "big")

_STRUCT_PREFIX = {"little": "<", "big": ">"}
_STRUCT_CODE_BY_WIDTH = {1: "B", 2: "H", 4: "I", 8: "Q"}


def pack_value(value: int, width: int, byte_order: str) -> bytes:
    if byte_order not in VALID_ORDERS:
        raise ValueError(f"byte_order deve essere 'little' o 'big', non '{byte_order}'")
    if width not in _STRUCT_CODE_BY_WIDTH:
        raise ValueError(f"larghezza non supportata: {width} bytes (supportati: 1, 2, 4, 8)")
    fmt = _STRUCT_PREFIX[byte_order] + _STRUCT_CODE_BY_WIDTH[width]
    return struct.pack(fmt, value)


def unpack_value(data: bytes, width: int, byte_order: str) -> int:
    fmt = _STRUCT_PREFIX[byte_order] + _STRUCT_CODE_BY_WIDTH[width]
    return struct.unpack(fmt, data)[0]


def repack(fields: list[dict], byte_order: str) -> bytes:
    """Ricostruisce un buffer di bytes contiguo dai valori strutturati,
    nell'ordine richiesto. I campi devono coprire un range contiguo
    senza sovrapposizioni a partire da offset 0 (stesso vincolo già
    applicato dai reader in fase di parsing)."""
    if not fields:
        return b""
    sorted_fields = sorted(fields, key=lambda f: f["offset"])
    out = bytearray()
    for f in sorted_fields:
        if f["offset"] != len(out):
            raise ValueError(
                f"campo a offset {f['offset']} non contiguo rispetto ai {len(out)} bytes già ricostruiti"
            )
        out += pack_value(f["value"], f["width"], byte_order)
    return bytes(out)
