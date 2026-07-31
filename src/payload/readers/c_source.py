"""
Compila un file .c con il toolchain configurato ed estrae i bytes di
una sezione dati dedicata, per popolare TableIR.data.

Il file .c deve definire i dati in una sezione con questo nome esatto:

    #include <stdint.h>
    const uint8_t table_data[] __attribute__((section("payload_table_data"))) = {
        0x0A, 0x1B,  // soglia min
        0x2C, 0x3D,  // soglia max
    };

Il nome sezione è fisso (non dipende dal nome tabella): questo .c viene
compilato solo per ESTRARNE i bytes, non è quello che finisce linkato
nel firmware — quella parte (con la sezione nominata per tabella e i
simboli __start_X/__stop_X) è responsabilità del writer 'obj', non di
questo reader. Vedi docs/PLUGINS.md.

I commenti // a fine riga sono estratti su base "best effort" per 'pld
view': non sono mai autorevoli sul contenuto — se il parsing testuale
fallisce per un .c più complesso della sintassi array-di-byte, i
commenti vengono semplicemente omessi senza errore, solo i bytes
realmente compilati contano."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from payload.core.errors import ReaderParseError, ToolchainExecutionError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

SECTION_NAME = "payload_table_data"

_COMMENT_RE = re.compile(r"//\s*(.+)$")
_HEX_RE = re.compile(r"0[xX][0-9a-fA-F]+")


class CSourceReader:
    """Compila un file .c (dati in una sezione dedicata, vedi docstring
    del modulo per la convenzione richiesta) tramite il toolchain
    configurato ed estrae i bytes compilati — il compilatore è sempre
    la fonte di verità sul contenuto, mai una reinterpretazione a mano
    del sorgente."""

    name = "c_source"
    extensions = [".c"]
    api_version = PLUGIN_API_VERSION
    default_writer = "obj"

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        toolchain = config.get("toolchain", {})
        compiler = toolchain.get("compiler", "gcc")
        compiler_flags = toolchain.get("compiler_flags", [])
        objcopy = toolchain.get("objcopy", "objcopy")

        # sottocartella PRIVATA dentro tmp/, non tmp/ stessa: quando questo
        # reader gira dentro una pipeline multi-stage, tmp/ è condivisa tra
        # più stage (vedi core/pipeline.py) — cancellare l'intera tmp/ a
        # fine parsing romperebbe gli stage successivi che se la aspettano
        # ancora lì.
        tmp = path.parent / "tmp" / "c_source_scratch"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            obj_path = tmp / "intermediate.o"
            bin_path = tmp / "extracted.bin"

            compile_cmd = [compiler, *compiler_flags, "-c", str(path), "-o", str(obj_path)]
            result = subprocess.run(compile_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ToolchainExecutionError(compile_cmd, result.returncode, result.stderr)

            extract_cmd = [
                objcopy, "-O", "binary",
                f"--only-section={SECTION_NAME}",
                str(obj_path), str(bin_path),
            ]
            result = subprocess.run(extract_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ToolchainExecutionError(extract_cmd, result.returncode, result.stderr)

            if not bin_path.exists() or bin_path.stat().st_size == 0:
                raise ReaderParseError(
                    path,
                    f"nessun dato trovato nella sezione '{SECTION_NAME}' — il .c deve "
                    f'definire i dati con __attribute__((section("{SECTION_NAME}")))',
                )

            data = bin_path.read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return TableIR(
            name=path.stem,
            data=data,
            source_path=path,
            source_format=self.name,
            comments=self._extract_comments_best_effort(path, len(data)),
        )

    def _extract_comments_best_effort(self, path: Path, data_len: int) -> list[tuple[int, str]]:
        try:
            comments = []
            offset = 0
            for line in path.read_text().splitlines():
                code_part, _, comment_part = line.partition("//")
                hex_values = _HEX_RE.findall(code_part)
                if comment_part.strip() and hex_values:
                    comments.append((offset, comment_part.strip()))
                offset += len(hex_values)
            return comments if offset == data_len else []  # sanity check: se non torna, non ci si fida
        except Exception:
            return []
