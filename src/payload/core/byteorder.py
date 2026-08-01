"""
Helper for handling endianness explicitly.

The problem: TableIR.data is already-packed bytes — no information
about where multi-byte fields start/end. A writer that just does
write_bytes(ir.data) can't reinterpret the order because it's blind to
field boundaries.

The solution: a reader that works with multi-byte values can (not
must) populate TableIR.extra["fields"] with the STRUCTURED values, not
just the already-packed bytes:

    ir.extra["fields"] = [
        {"offset": 0, "width": 2, "value": 0x1234},
        {"offset": 2, "width": 4, "value": 0xDEADBEEF},
    ]

A writer that wants an order different from ir.byte_order can call
repack() on this list to get bytes in its own target order, without
having to blindly reinterpret raw bytes.
"""
from __future__ import annotations

import struct

VALID_ORDERS = ("little", "big")

_STRUCT_PREFIX = {"little": "<", "big": ">"}
_STRUCT_CODE_BY_WIDTH = {1: "B", 2: "H", 4: "I", 8: "Q"}


def pack_value(value: int, width: int, byte_order: str) -> bytes:
    if byte_order not in VALID_ORDERS:
        raise ValueError(f"byte_order must be 'little' or 'big', not '{byte_order}'")
    if width not in _STRUCT_CODE_BY_WIDTH:
        raise ValueError(f"unsupported width: {width} bytes (supported: 1, 2, 4, 8)")
    fmt = _STRUCT_PREFIX[byte_order] + _STRUCT_CODE_BY_WIDTH[width]
    return struct.pack(fmt, value)


def unpack_value(data: bytes, width: int, byte_order: str) -> int:
    fmt = _STRUCT_PREFIX[byte_order] + _STRUCT_CODE_BY_WIDTH[width]
    return struct.unpack(fmt, data)[0]


def repack(fields: list[dict], byte_order: str) -> bytes:
    """Rebuilds a contiguous byte buffer from the structured values, in
    the requested order. Fields must cover a contiguous range with no
    overlaps starting at offset 0 (same constraint already enforced by
    readers during parsing)."""
    if not fields:
        return b""
    sorted_fields = sorted(fields, key=lambda f: f["offset"])
    out = bytearray()
    for f in sorted_fields:
        if f["offset"] != len(out):
            raise ValueError(
                f"field at offset {f['offset']} isn't contiguous with the {len(out)} bytes already rebuilt"
            )
        out += pack_value(f["value"], f["width"], byte_order)
    return bytes(out)
