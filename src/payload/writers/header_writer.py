"""
Writer: header C con i bytes di TableIR.data in un array 'static const
uint8_t'. Alternativa a costo di configurazione zero a obj_writer.py
(che richiede objcopy + toolchain.objcopy_target/objcopy_arch): qui
basta '#include' il file generato in una singola translation unit.

'static const', non 'extern': pensato per essere incluso in UN SOLO
file .c — evita errori ODR/ridefinizione se il file viene incluso per
sbaglio due volte. Se serve condividere l'array tra più .c (più
translation unit), serve una coppia .h ('extern const uint8_t
NAME[];') + .c (con la definizione vera) scritta a mano — fuori scope
per questo writer, che genera un header autosufficiente.

byte_order è irrilevante qui: l'array è sempre 'uint8_t[]', un byte per
elemento — nessun repack() come invece serve a bin_writer.py per un
dump binario di valori multi-byte."""
from __future__ import annotations

import re

from pathlib import Path

from payload.core.errors import WriterEmitError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_identifier(name: str, fallback_prefix: str) -> str:
    """Sanitizza in un identificatore C valido. Oltre al caso ovvio
    (inizia con una cifra), prefissa anche se il risultato è vuoto o
    inizia con '_' — un nome di soli caratteri invalidi (es. '???')
    sanitizzerebbe altrimenti in '___', un identificatore riservato al
    compilatore/libreria standard (§7.1.3)."""
    sanitized = _INVALID_CHARS_RE.sub("_", name)
    if not sanitized or sanitized[0].isdigit() or sanitized[0] == "_":
        sanitized = f"{fallback_prefix}_{sanitized}" if sanitized else fallback_prefix
    return sanitized


class HeaderWriter:
    """Genera un header C autosufficiente: '#ifndef' guard, '#include
    <stdint.h>', un array 'static const uint8_t NOME[N] = {...};' con
    i bytes di ir.data, uno per riga, con eventuali ir.comments a fine
    riga. Nome array e include guard derivati da ir.name (sanitizzato),
    override tramite [plugin.header] o --opt (--opt vince)."""

    name = "header"
    extension = ".h"
    api_version = PLUGIN_API_VERSION
    compatible_readers = None

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        if not ir.data:
            raise WriterEmitError(self.name, "TableIR.data è vuoto: un array 'uint8_t[0]' non è C portabile")

        cli_opts = config.get("cli_opts", {})
        plugin_cfg = config.get("plugin", {}).get(self.name, {})

        array_name = (
            cli_opts.get("array_name")
            or plugin_cfg.get("array_name")
            or _sanitize_identifier(ir.name, "table")
        )
        include_guard = (
            cli_opts.get("include_guard")
            or plugin_cfg.get("include_guard")
            or f"{_sanitize_identifier(ir.name, 'TABLE').upper()}_H"
        )

        comment_by_offset = dict(ir.comments)
        lines = []
        for offset, byte in enumerate(ir.data):
            line = f"    0x{byte:02X},"
            comment = comment_by_offset.get(offset)
            if comment:
                line += f"  // {comment}"
            lines.append(line)
        array_body = "\n".join(lines)

        content = (
            f"#ifndef {include_guard}\n"
            f"#define {include_guard}\n"
            "\n"
            "#include <stdint.h>\n"
            "\n"
            f"/* Generato da payload — {len(ir.data)} bytes, sorgente: {ir.source_path.name} */\n"
            "/* byte_order irrilevante qui: array di uint8_t, un byte per elemento */\n"
            f"static const uint8_t {array_name}[{len(ir.data)}] = {{\n"
            f"{array_body}\n"
            "};\n"
            "\n"
            f"#endif /* {include_guard} */\n"
        )
        out_path.write_text(content)
        return out_path
