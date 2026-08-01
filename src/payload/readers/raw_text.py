"""
Reader di esempio: formato testuale con valori byte in esadecimale e
commenti opzionali. Sintassi:

    # commento di linea intera, ignorato
    0x0A, 0x1B          # commento a fine riga, salvato in TableIR.comments
    0x2C, 0x3D
"""
from __future__ import annotations

from pathlib import Path

from payload.core.errors import ReaderParseError
from payload.core.ir import TableIR


class RawTextReader:
    """Formato testuale minimale: valori byte in esadecimale separati da
    virgola, un commento opzionale a fine riga dopo '#'.

    Esempio:
        # commento di linea intera, ignorato
        0x0A, 0x1B          # commento a fine riga, salvato in TableIR.comments
        0x2C, 0x3D

    Ogni valore deve stare in un singolo byte (0-255). Per valori
    multi-byte con controllo dell'endianness, usa il reader 'csv'."""

    name = "raw_text"
    extensions = [".raw", ".txt"]
    api_version = "1.0"
    default_writer = "bin"  # formato dati grezzo -> dump binario grezzo, scelta naturale

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
            if not code_part:  # pragma: no cover - difesa: 'line' già garantita non-vuota e non-'#'-iniziale sopra, quindi code_part non può essere vuoto
                continue

            offset_before = len(data)
            for token in (t.strip() for t in code_part.split(",")):
                if not token:
                    continue
                try:
                    data.append(int(token, 16))
                except ValueError as e:
                    raise ReaderParseError(
                        path, f"riga {lineno}: valore non valido '{token}'"
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
        """Estensione opzionale (vedi src/payload/docs/BATCH.md): legge
        N file NELL'ORDINE DATO da 'paths' (già risolto/ordinato dal
        chiamante — [[batch_table]]) e li concatena come se fossero un
        unico file più lungo, riusando la stessa logica riga-per-riga
        di parse(). Gli offset dei commenti sono cumulativi attraverso
        i file, non locali a ciascuno."""
        data = bytearray()
        comments: list[tuple[int, str]] = []

        for path in paths:
            for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                code_part, _, comment_part = line.partition("#")
                code_part = code_part.strip()
                if not code_part:  # pragma: no cover - difesa, stessa garanzia di parse()
                    continue

                offset_before = len(data)
                for token in (t.strip() for t in code_part.split(",")):
                    if not token:
                        continue
                    try:
                        data.append(int(token, 16))
                    except ValueError as e:
                        raise ReaderParseError(
                            path, f"riga {lineno}: valore non valido '{token}'"
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
