"""
Benchmark: build-all su 100 tabelle da ~1MB, jobs=1 vs jobs=N.

Bypassa typer/rich (non installabili offline in questo sandbox)
e chiama direttamente payload.core.pipeline.build(), che è dove conta
la concorrenza reale — è la stessa funzione usata da 'pld build-all'.

Reader/writer usati qui sono volutamente leggeri (letture/scritture
binarie dirette, senza parsing) per isolare il costo della PIPELINE
(hashing per la cache, I/O, orchestrazione) dal costo di un parser
specifico. La nota onesta sui limiti di questo benchmark è in fondo
al file.
"""
from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from payload.core.cache import BuildCache
from payload.core.ir import PLUGIN_API_VERSION, TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry

N_TABLES = 100
TABLE_SIZE_BYTES = 1_000_000
BENCH_DIR = Path("/tmp/payload_benchmark")


class BenchReader:
    """Legge il file binario così com'è, senza parsing: isola il costo
    puro di I/O + hashing dal costo di un parser specifico."""

    name = "bench_reader"
    extensions = [".bench"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        return False

    def parse(self, path: Path, config: dict) -> TableIR:
        return TableIR(
            name=path.stem,
            data=path.read_bytes(),
            source_path=path,
            source_format=self.name,
        )


class BenchWriter:
    name = "bench_writer"
    extension = ".out"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        out_path.write_bytes(ir.data)
        return out_path


class _FakeDefaults:
    def __init__(self, writer="bench_writer"):
        self.writer = writer


class _FakeConfig:
    """Duck-typing minimale: pipeline.build() usa solo .defaults.writer
    e .model_dump() — bypassato qui per non dipendere dal core config
    reale, ma la struttura è identica."""

    def __init__(self):
        self.defaults = _FakeDefaults()

    def model_dump(self) -> dict:
        return {"defaults": {"writer": self.defaults.writer}}


def setup_tables() -> list[Path]:
    if BENCH_DIR.exists():
        shutil.rmtree(BENCH_DIR)
    src_dir = BENCH_DIR / "sources"
    src_dir.mkdir(parents=True)
    paths = []
    for i in range(N_TABLES):
        p = src_dir / f"table_{i:03d}.bench"
        p.write_bytes(os.urandom(TABLE_SIZE_BYTES))
        paths.append(p)
    return paths


def make_registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_reader(BenchReader())
    r.register_writer(BenchWriter())
    return r


def run_sequential(sources: list[Path], out_dir: Path) -> float:
    registry = make_registry()
    config = _FakeConfig()
    cache = BuildCache(BENCH_DIR / "cache_seq")

    start = time.perf_counter()
    for src in sources:
        build(src, registry, config, out_dir, cache=cache, force=True)
    return time.perf_counter() - start


def run_parallel(sources: list[Path], out_dir: Path, jobs: int) -> float:
    registry = make_registry()
    config = _FakeConfig()
    cache = BuildCache(BENCH_DIR / f"cache_par_{jobs}")

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [
            executor.submit(build, src, registry, config, out_dir, cache=cache, force=True)
            for src in sources
        ]
        for f in as_completed(futures):
            f.result()  # propaga eventuali eccezioni
    return time.perf_counter() - start


def main():
    print(f"Setup: genero {N_TABLES} tabelle da {TABLE_SIZE_BYTES:,} bytes...")
    sources = setup_tables()
    total_mb = (N_TABLES * TABLE_SIZE_BYTES) / (1024 * 1024)
    print(f"Totale dati: {total_mb:.1f} MB\n")

    cpu_count = os.cpu_count() or 1
    print(f"CPU disponibili (os.cpu_count()): {cpu_count}\n")

    out_seq = BENCH_DIR / "out_seq"
    t_seq = run_sequential(sources, out_seq)
    print(f"jobs=1  (sequenziale) : {t_seq:6.3f}s")

    results = [(1, t_seq)]
    for jobs in (2, 4, 8, cpu_count):
        if jobs in [r[0] for r in results]:
            continue
        out_par = BENCH_DIR / f"out_par_{jobs}"
        t_par = run_parallel(sources, out_par, jobs)
        speedup = t_seq / t_par
        print(f"jobs={jobs:<3}(parallelo)    : {t_par:6.3f}s   speedup: {speedup:.2f}x")
        results.append((jobs, t_par))

    print("\nNota: BenchReader/BenchWriter fanno solo I/O + hashing sha256 per la")
    print("cache (sha256 in CPython rilascia il GIL per buffer grandi, quindi")
    print("beneficia già di thread multipli). Con un reader 'c_source' reale che")
    print("invoca un compilatore esterno via subprocess, il guadagno sarebbe")
    print("MAGGIORE: subprocess.run() rilascia il GIL per l'intera attesa del")
    print("processo esterno, quindi N compilazioni in parallelo si sovrappongono")
    print("quasi per intero, non solo per la parte di hashing/IO.")


if __name__ == "__main__":
    main()
