"""File-level operations shared by the CLI and the web file browser:
byte-level comparison of two files, content search (text or byte
pattern), and a small binary analysis (entropy / byte frequency /
magic). CLI-agnostic — the CLI prints them, the web routes serialize
them. All reads are capped so a huge file never loads fully into
memory."""
from __future__ import annotations

import math
from pathlib import Path

from payload.core.config import load_config

# Same cap as the web reader: never process more than this per file.
READ_CAP = 4 * 1024 * 1024


def _read_capped(p: Path) -> tuple[bytes, int]:
    size = p.stat().st_size
    if not size:
        return b"", 0
    with p.open("rb") as f:
        return f.read(READ_CAP if size > READ_CAP else size), size


def _magic_of(data: bytes) -> list[str]:
    magic = []
    if data.startswith(b"\x7fELF"):
        magic.append("ELF executable")
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        magic.append("PNG image")
    elif data.startswith(b"\xff\xd8\xff"):
        magic.append("JPEG image")
    elif data.startswith(b"PK\x03\x04"):
        magic.append("ZIP archive")
    elif data.startswith(b"\x1f\x8b"):
        magic.append("GZIP archive")
    elif data.startswith(b"%PDF-"):
        magic.append("PDF document")
    elif data.startswith(b"BM"):
        magic.append("BMP image")
    elif data.startswith(b"#!/"):
        magic.append("script (shebang)")
    return magic


def compare_files(a: Path, b: Path) -> dict:
    """Byte-level comparison of two files: common prefix/suffix and the
    runs where they differ (aligned from the start). When the sizes
    differ, the extra tail of the longer file is reported as a run."""
    da, la = _read_capped(a)
    db, lb = _read_capped(b)
    n = min(len(da), len(db))
    i = 0
    while i < n and da[i] == db[i]:
        i += 1
    prefix = i
    j = 0
    while j < n - i and da[n - 1 - j] == db[n - 1 - j]:
        j += 1
    suffix = j

    runs = []
    start = None
    for k in range(prefix, n - suffix):
        if da[k] != db[k]:
            if start is None:
                start = k
        elif start is not None:
            runs.append({"offset": start, "length": k - start})
            start = None
    if start is not None:
        runs.append({"offset": start, "length": n - suffix - start})
    if la != lb:
        runs.append({"offset": n, "length": abs(la - lb), "file": "a" if la > lb else "b"})

    return {
        "a": str(a), "b": str(b), "a_size": la, "b_size": lb,
        "equal": la == lb and not runs,
        "prefix": prefix, "suffix": suffix,
        "runs": runs,
        "truncated": max(la, lb) > READ_CAP,
    }


def _is_internal_path(root: Path, rel: str) -> bool:
    """Skip the project infrastructure, like discovery does: hidden
    entries, plugins/, output_dir and cache_dir (config, best-effort)."""
    parts = Path(rel).parts
    if any(part.startswith(".") for part in parts):
        return True
    try:
        cfg = load_config(root)
        excluded = {cfg.defaults.output_dir, cfg.defaults.cache_dir, "plugins"}
    except Exception:
        excluded = {"build", ".payload_cache", "plugins"}
    return bool(parts) and parts[0] in excluded


def search_files(root: Path, pattern: bytes, max_results: int = 50, start: Path | None = None) -> dict:
    """Find 'pattern' in every file under 'start' (default: the whole
    project, best-effort internal exclusions). Returns matches with
    project-relative paths, byte offsets, and the matching bytes as
    hex+ascii for a compact preview."""
    base = start or root
    matches = []
    searched = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _is_internal_path(root, rel):
            continue
        searched += 1
        data, _ = _read_capped(p)
        idx = 0
        while True:
            idx = data.find(pattern, idx)
            if idx < 0:
                break
            shown = data[idx:idx + min(len(pattern), 16)]
            matches.append({
                "path": rel,
                "offset": idx,
                "hex": shown.hex(" ").upper(),
                "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in shown),
            })
            if len(matches) >= max_results:
                return {"matches": matches, "truncated": True, "searched": searched}
            idx += max(1, len(pattern))
    return {"matches": matches, "truncated": False, "searched": searched}


def analyze_file(path: Path) -> dict:
    """Shannon entropy, printable-byte ratio, byte frequency and magic
    candidates for a file — the 'is this text, code, or raw data?' answer."""
    data, size = _read_capped(path)
    n = len(data)
    out = {"size": size, "analyzed": n, "capped": size > n}
    if n == 0:
        return {**out, "entropy": 0.0, "printable_ratio": 0.0, "distinct": 0, "null_ratio": 0.0, "freq": [], "magic": [], "ascii_runs": 0}

    freq = [0] * 256
    for b in data:
        freq[b] += 1
    entropy = -sum((c / n) * math.log2(c / n) for c in freq if c)
    printable = sum(freq[b] for b in list(range(32, 127)) + [9, 10, 13])

    runs = 0
    cur = 0
    for b in data:
        if 32 <= b < 127:
            cur += 1
        else:
            if cur >= 4:
                runs += 1
            cur = 0
    if cur >= 4:
        runs += 1

    return {
        **out,
        "entropy": round(entropy, 3),
        "printable_ratio": round(printable / n, 3),
        "distinct": len([c for c in freq if c]),
        "null_ratio": round(freq[0] / n, 3),
        "freq": [[b, freq[b]] for b in range(256) if freq[b]],
        "magic": _magic_of(data),
        "ascii_runs": runs,
    }
