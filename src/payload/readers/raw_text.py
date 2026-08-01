"""
Example reader: text format with hexadecimal byte values and optional
comments. Syntax:

    # whole-line comment, ignored
    0x0A, 0x1B          # end-of-line comment, saved in TableIR.comments
    0x2C, 0x3D
"""
from __future__ import annotations

from pathlib import Path

from payload.core.errors import ReaderParseError
from payload.core.ir import TableIR


class RawTextReader:
    """Minimal text format: hexadecimal byte values separated by
    commas, an optional end-of-line comment after '#'.

    Example:
        # whole-line comment, ignored
        0x0A, 0x1B          # end-of-line comment, saved in TableIR.comments
        0x2C, 0x3D

    Every value must fit in a single byte (0-255). For multi-byte
    values with endianness control, use the 'csv' reader."""

    name = "raw_text"
    extensions = [".raw", ".txt"]
    api_version = "1.0"
    default_writer = "bin"  # raw data format -> raw binary dump, the natural choice

    def sniff(self, path: Path) -> bool:
        try:
            head = path.read_text(errors="ignore")[:200]
        except OSError:
            return False
        return "0x" in head

    def parse(self, path: Path, config: dict) -> TableIR:
        data = bytearray()
        comments: list[tuple[int, str]] = []

        for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            code_part, _, comment_part = line.partition("#")
            code_part = code_part.strip()
            if not code_part:  # pragma: no cover - defensive: 'line' is already guaranteed non-empty and not starting with '#' above, so code_part can't be empty
                continue

            offset_before = len(data)
            for token in (t.strip() for t in code_part.split(",")):
                if not token:
                    continue
                try:
                    data.append(int(token, 16))
                except ValueError as e:
                    raise ReaderParseError(
                        path, f"line {lineno}: invalid value '{token}'"
                    ) from e

            if comment_part.strip():
                comments.append((offset_before, comment_part.strip()))

        return TableIR(
            name=path.stem,
            data=bytes(data),
            source_path=path,
            source_format=self.name,
            comments=comments,
        )

    def parse_many(self, paths: list[Path], config: dict) -> TableIR:
        """Optional extension (see src/payload/docs/BATCH.md): reads N
        files in the ORDER GIVEN by 'paths' (already resolved/ordered
        by the caller — [[batch_table]]) and concatenates them as if
        they were a single longer file, reusing the same line-by-line
        logic as parse(). Comment offsets are cumulative across the
        files, not local to each one."""
        data = bytearray()
        comments: list[tuple[int, str]] = []

        for path in paths:
            for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                code_part, _, comment_part = line.partition("#")
                code_part = code_part.strip()
                if not code_part:  # pragma: no cover - defensive, same guarantee as parse()
                    continue

                offset_before = len(data)
                for token in (t.strip() for t in code_part.split(",")):
                    if not token:
                        continue
                    try:
                        data.append(int(token, 16))
                    except ValueError as e:
                        raise ReaderParseError(
                            path, f"line {lineno}: invalid value '{token}'"
                        ) from e

                if comment_part.strip():
                    comments.append((offset_before, comment_part.strip()))

        return TableIR(
            name=paths[0].stem,
            data=bytes(data),
            source_path=paths[0],
            source_format=self.name,
            comments=comments,
        )
