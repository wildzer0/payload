"""
Test del reader c_source e writer obj CON toolchain reale (gcc/objcopy),
non mockato — è l'unico modo per essere sicuri che i comandi generati
siano davvero corretti, non solo plausibili.

Se gcc/objcopy non sono disponibili nell'ambiente di CI/sviluppo,
questi test vengono saltati (non falliti) — vedi il fixture 'toolchain_available'.
"""
import shutil
import subprocess

import pytest

from payload.core.errors import ReaderParseError, ToolchainExecutionError, WriterEmitError
from payload.core.ir import TableIR
from payload.readers.c_source import CSourceReader
from payload.writers.obj_writer import ObjWriter, section_name_for

pytestmark = pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("objcopy") is None,
    reason="richiede gcc e objcopy reali nell'ambiente",
)


def _host_objcopy_target_arch() -> tuple[str, str]:
    """Determina target/arch objcopy per l'host corrente, così i test
    girano su qualsiasi macchina (non solo x86_64) senza hardcoding."""
    result = subprocess.run(["objcopy", "--info"], capture_output=True, text=True)
    output = result.stdout + result.stderr
    if "elf64-x86-64" in output:
        return "elf64-x86-64", "i386:x86-64"
    if "elf32-i386" in output:
        return "elf32-i386", "i386"
    if "elf64-littleaarch64" in output:
        return "elf64-littleaarch64", "aarch64"
    pytest.skip("architettura host non riconosciuta per il test, aggiungi un caso")


@pytest.fixture
def toolchain_config():
    return {"toolchain": {"compiler": "gcc", "compiler_flags": [], "objcopy": "objcopy"}}


@pytest.fixture
def obj_config():
    target, arch = _host_objcopy_target_arch()
    return {"toolchain": {"objcopy": "objcopy", "objcopy_target": target, "objcopy_arch": arch}}


C_SOURCE_VALID = '''#include <stdint.h>

const uint8_t table_data[] __attribute__((section("payload_table_data"))) = {
    0x0A, 0x1B,  // soglia min
    0x2C, 0x3D,  // soglia max
};
'''


def test_c_source_reader_compiles_and_extracts_bytes(tmp_path, toolchain_config):
    c_file = tmp_path / "temp_table.c"
    c_file.write_text(C_SOURCE_VALID)

    ir = CSourceReader().parse(c_file, toolchain_config)

    assert ir.data == bytes([0x0A, 0x1B, 0x2C, 0x3D])
    assert ir.source_format == "c_source"


def test_c_source_reader_extracts_comments_best_effort(tmp_path, toolchain_config):
    c_file = tmp_path / "t.c"
    c_file.write_text(C_SOURCE_VALID)

    ir = CSourceReader().parse(c_file, toolchain_config)

    assert ir.comments == [(0, "soglia min"), (2, "soglia max")]


def test_c_source_reader_compile_error_raises_with_stderr(tmp_path, toolchain_config):
    c_file = tmp_path / "bad.c"
    c_file.write_text("questo non e C valido !!! {{{")

    with pytest.raises(ToolchainExecutionError) as exc_info:
        CSourceReader().parse(c_file, toolchain_config)
    assert exc_info.value.context["returncode"] != 0


def test_c_source_reader_missing_section_raises_clear_error(tmp_path, toolchain_config):
    c_file = tmp_path / "nosection.c"
    c_file.write_text("const int x = 42;\n")

    with pytest.raises(ReaderParseError):
        CSourceReader().parse(c_file, toolchain_config)


def test_obj_writer_produces_real_linkable_object(tmp_path, obj_config):
    ir = TableIR(name="temp_table", data=bytes([0x0A, 0x1B, 0x2C, 0x3D]), source_path=tmp_path, source_format="fake")
    out_path = tmp_path / "temp_table.o"

    ObjWriter().emit(ir, out_path, obj_config)

    assert out_path.exists()
    # verifica con objdump che la sezione esista davvero col nome giusto e contenga i byte giusti
    result = subprocess.run(
        ["objdump", "-s", "-j", "table_temp_table", str(out_path)], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "0a1b2c3d" in result.stdout.replace(" ", "")


def test_obj_writer_missing_target_config_raises(tmp_path):
    ir = TableIR(name="t", data=b"\x01\x02", source_path=tmp_path, source_format="fake")
    with pytest.raises(WriterEmitError):
        ObjWriter().emit(ir, tmp_path / "out.o", {"toolchain": {}})


def test_obj_writer_output_actually_links_with_start_stop_symbols(tmp_path, obj_config):
    """Il test più importante: un vero programma C linka il .o prodotto
    e legge i dati tramite i simboli __start_/__stop_ generati dal
    linker — è la promessa centrale di tutto il writer 'obj'."""
    ir = TableIR(name="link_test", data=bytes([0xAA, 0xBB, 0xCC]), source_path=tmp_path, source_format="fake")
    obj_path = tmp_path / "link_test.o"
    ObjWriter().emit(ir, obj_path, obj_config)

    section = section_name_for("link_test")
    main_c = tmp_path / "main.c"
    main_c.write_text(f'''
#include <stdio.h>
extern unsigned char __start_{section}[];
extern unsigned char __stop_{section}[];
int main() {{
    size_t size = __stop_{section} - __start_{section};
    printf("%zu", size);
    for (size_t i = 0; i < size; i++) printf(",%02x", __start_{section}[i]);
    return 0;
}}
''')
    exe_path = tmp_path / "test_link"
    compile_result = subprocess.run(
        ["gcc", str(main_c), str(obj_path), "-o", str(exe_path)],
        capture_output=True, text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    run_result = subprocess.run([str(exe_path)], capture_output=True, text=True)
    assert run_result.stdout == "3,aa,bb,cc"


def test_section_name_sanitizes_invalid_chars():
    assert section_name_for("temp_table") == "table_temp_table"
    assert section_name_for("temp-table") == "table_temp_table"
    assert section_name_for("temp.table") == "table_temp_table"


def test_section_name_never_starts_with_digit():
    name = section_name_for("123start")
    assert not name[0].isdigit()
    assert name == "table_123start"
