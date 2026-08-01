"""
Produce un .o linkabile con i dati in una sezione dedicata, nominata a
partire dal nome tabella (sanitizzato in un identificatore C valido).

Questo è quello che rende disponibili i simboli
__start_<sezione>/__stop_<sezione> quando il .o viene linkato nel
firmware finale: GNU ld li genera automaticamente per ogni sezione
"orfana" il cui nome è un identificatore C valido — non c'è bisogno di
un linker script custom né di generare quei simboli qui (documentato
nel manuale di GNU ld, sezione "orphan sections").

Richiede 'toolchain.objcopy_target' (bfd target del tuo MCU, es.
'elf32-littlearm' per ARM) e 'toolchain.objcopy_arch' (es. 'arm') in
table-tool.toml — 'objcopy -I binary' non ha un default universale,
dipende dal target."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from payload.core.errors import ToolchainExecutionError, WriterEmitError
from payload.core.ir import PLUGIN_API_VERSION, TableIR

_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")


def section_name_for(table_name: str) -> str:
    """Sanitizza il nome tabella in un identificatore C valido, prefissato
    con 'table_' — necessario perché GNU ld genera i simboli
    __start_X/__stop_X solo se X è un identificatore C valido. Il
    prefisso da solo basta a garantire che non inizi mai con una cifra,
    qualunque sia il nome tabella originale."""
    sanitized = _INVALID_CHARS_RE.sub("_", table_name)
    return f"table_{sanitized}"


class ObjWriter:
    """Scrive un .o con TableIR.data in una sezione nominata
    'table_<nome tabella>' (sanitizzata), pronta per essere linkata nel
    firmware con i simboli __start_/__stop_ generati dal linker finale.
    Vedi il docstring del modulo per i requisiti di config."""

    name = "obj"
    extension = ".o"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        toolchain = config.get("toolchain", {})
        objcopy = toolchain.get("objcopy", "objcopy")
        target = toolchain.get("objcopy_target") or ""
        arch = toolchain.get("objcopy_arch") or ""

        if not target or not arch:
            raise WriterEmitError(
                self.name,
                "richiede 'toolchain.objcopy_target' e 'toolchain.objcopy_arch' in "
                'table-tool.toml (es. objcopy_target = "elf32-littlearm", objcopy_arch = "arm")',
            )

        section = section_name_for(ir.name)

        # sottocartella PRIVATA, stesso motivo di c_source.py: tmp/ è
        # condivisa tra più stage di una pipeline, non va cancellata tutta.
        tmp = ir.source_path.parent / "tmp" / "obj_writer_scratch"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            bin_path = tmp / "input.bin"
            bin_path.write_bytes(ir.data)

            cmd = [
                objcopy, "-I", "binary", "-O", target, "-B", arch,
                "--rename-section", f".data={section},alloc,load,readonly,data,contents",
                str(bin_path), str(out_path),
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError as e:
                raise ToolchainExecutionError(
                    cmd, -1, f"eseguibile non trovato: '{objcopy}' ({e})"
                ) from e
            if result.returncode != 0:
                raise ToolchainExecutionError(cmd, result.returncode, result.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return out_path
