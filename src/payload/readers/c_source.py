"""
Compiles a .c file with the configured toolchain and extracts the
bytes of a dedicated data section, to populate TableIR.data.

The .c file must define the data in a section with this exact name:

    #include <stdint.h>
    const uint8_t table_data[] __attribute__((section("payload_table_data"))) = {
        0x0A, 0x1B,  // min threshold
        0x2C, 0x3D,  // max threshold
    };

The section name is fixed (doesn't depend on the table name): this .c
file is compiled only to EXTRACT its bytes, it's not what ends up
linked into the firmware — that part (with the per-table section name
and the __start_X/__stop_X symbols) is the 'obj' writer's
responsibility, not this reader's. See src/payload/docs/PLUGINS.md.

The trailing // comments are extracted on a "best effort" basis for
'pld view': they're never authoritative about the content — if text
parsing fails for a .c more complex than the array-of-bytes syntax,
the comments are simply omitted without an error, only the bytes
actually compiled matter."""
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
    """Compiles a .c file (data in a dedicated section, see the module
    docstring for the required convention) with the configured
    toolchain and extracts the compiled bytes — the compiler is always
    the source of truth for the content, never a hand-rolled
    reinterpretation of the source."""

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

        # PRIVATE subfolder inside tmp/, not tmp/ itself: when this
        # reader runs inside a multi-stage pipeline, tmp/ is shared
        # across several stages (see core/pipeline.py) — deleting the
        # whole tmp/ at the end of parsing would break later stages
        # that still expect it there.
        tmp = path.parent / "tmp" / "c_source_scratch"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            obj_path = tmp / "intermediate.o"
            bin_path = tmp / "extracted.bin"

            compile_cmd = [compiler, *compiler_flags, "-c", str(path), "-o", str(obj_path)]
            try:
                result = subprocess.run(compile_cmd, capture_output=True, text=True)
            except FileNotFoundError as e:
                raise ToolchainExecutionError(
                    compile_cmd, -1, f"executable not found: '{compiler}' ({e})"
                ) from e
            if result.returncode != 0:
                raise ToolchainExecutionError(compile_cmd, result.returncode, result.stderr)

            extract_cmd = [
                objcopy, "-O", "binary",
                f"--only-section={SECTION_NAME}",
                str(obj_path), str(bin_path),
            ]
            try:
                result = subprocess.run(extract_cmd, capture_output=True, text=True)
            except FileNotFoundError as e:
                raise ToolchainExecutionError(
                    extract_cmd, -1, f"executable not found: '{objcopy}' ({e})"
                ) from e
            if result.returncode != 0:
                raise ToolchainExecutionError(extract_cmd, result.returncode, result.stderr)

            if not bin_path.exists() or bin_path.stat().st_size == 0:
                raise ReaderParseError(
                    path,
                    f"no data found in section '{SECTION_NAME}' — the .c file must "
                    f'define the data with __attribute__((section("{SECTION_NAME}")))',
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
            return comments if offset == data_len else []  # sanity check: if it doesn't add up, don't trust it
        except Exception:
            return []
