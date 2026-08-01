"""
Specifica e validazione della pipeline. Vedi src/payload/docs/PIPELINE.md per il
design completo e il perché delle regole di alternanza.

Tre tipi di stage:
- ReaderStage: file -> dati in memoria (TableIR)
- WriterStage: dati in memoria -> file
- ExecStage: file -> file (comando esterno)

Regole di alternanza, verificate PRIMA di eseguire qualunque stage:
1. il primo stage deve essere un reader
2. un reader deve essere seguito immediatamente da un writer
3. dopo un writer: reader, exec, un altro writer, o fine pipeline
4. dopo un exec: reader, exec, o fine pipeline
5. almeno 2 stage (reader + writer)
6. fan-out: un reader può essere seguito da PIÙ writer consecutivi (tutti
   alimentati dalla stessa IR parsata una sola volta) — ma un gruppo di
   2+ writer consecutivi dev'essere l'ultimo della pipeline: non può
   esserci un reader/exec dopo un fan-out. Un gruppo di un solo writer
   resta senza questa restrizione (comportamento di sempre: reader/exec
   possono seguirlo). Vedi src/payload/docs/PIPELINE.md, sezione Fan-out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from payload.core.errors import InvalidPipelineError

VALID_ON_ERROR = ("fail", "warn")


@dataclass
class ReaderStage:
    name: str
    kind: str = "reader"


@dataclass
class WriterStage:
    name: str
    kind: str = "writer"


@dataclass
class ExecStage:
    command: str
    kind: str = "exec"
    on_error: str = "fail"  # "fail" | "warn"
    # richiesto SOLO se questo exec è l'ultimo stage della pipeline
    # (serve a determinare l'estensione del file di output finale,
    # che per reader/writer si legge da writer.extension)
    output_extension: str | None = None


Stage = Union[ReaderStage, WriterStage, ExecStage]


def parse_stage(raw: dict, index: int) -> Stage:
    if not isinstance(raw, dict):
        raise InvalidPipelineError(index, "ogni stage deve essere una tabella TOML, es. { type = \"reader\", name = \"...\" }")

    stage_type = raw.get("type")

    if stage_type == "reader":
        name = raw.get("name")
        if not name:
            raise InvalidPipelineError(index, "stage 'reader' richiede 'name'")
        return ReaderStage(name=name)

    if stage_type == "writer":
        name = raw.get("name")
        if not name:
            raise InvalidPipelineError(index, "stage 'writer' richiede 'name'")
        return WriterStage(name=name)

    if stage_type == "exec":
        command = raw.get("command")
        if not command:
            raise InvalidPipelineError(index, "stage 'exec' richiede 'command'")
        on_error = raw.get("on_error", "fail")
        if on_error not in VALID_ON_ERROR:
            raise InvalidPipelineError(
                index, f"'on_error' deve essere 'fail' o 'warn', trovato '{on_error}'"
            )
        return ExecStage(
            command=command,
            on_error=on_error,
            output_extension=raw.get("output_extension"),
        )

    raise InvalidPipelineError(
        index, f"tipo di stage sconosciuto: '{stage_type}' (atteso: reader | writer | exec)"
    )


def validate_alternation(stages: list[Stage]) -> None:
    if len(stages) < 2:
        raise InvalidPipelineError(0, "la pipeline deve avere almeno 2 stage (un reader e un writer)")

    if not isinstance(stages[0], ReaderStage):
        raise InvalidPipelineError(0, "il primo stage deve essere un 'reader'")

    n = len(stages)
    i = 0
    while i < n:
        stage = stages[i]
        if isinstance(stage, ReaderStage):
            if i + 1 >= n:
                raise InvalidPipelineError(i, "un 'reader' non può essere l'ultimo stage della pipeline")
            if not isinstance(stages[i + 1], WriterStage):
                raise InvalidPipelineError(
                    i, "un 'reader' deve essere seguito immediatamente da un 'writer'"
                )
            i += 1
        elif isinstance(stage, WriterStage):
            run_start = i
            while i < n and isinstance(stages[i], WriterStage):
                i += 1
            run_end = i - 1
            if (run_end - run_start + 1) >= 2 and run_end != n - 1:
                raise InvalidPipelineError(
                    run_end,
                    "un gruppo di più 'writer' consecutivi (fan-out) deve essere l'ultimo "
                    "gruppo della pipeline — nessun 'reader'/'exec' può seguirlo",
                )
        else:  # ExecStage
            i += 1

    last = stages[-1]
    if isinstance(last, ExecStage) and not last.output_extension:
        raise InvalidPipelineError(
            len(stages) - 1,
            "l'ultimo stage 'exec' di una pipeline deve dichiarare 'output_extension' "
            "(serve a determinare il nome del file finale, es. output_extension = \".signed.bin\")",
        )


@dataclass
class PipelineSpec:
    stages: list[Stage] = field(default_factory=list)

    @classmethod
    def from_raw_stages(cls, raw_stages: list) -> "PipelineSpec":
        if not isinstance(raw_stages, list):
            raise InvalidPipelineError(0, "'pipeline.stages' deve essere una lista di stage")
        stages = [parse_stage(raw, i) for i, raw in enumerate(raw_stages)]
        validate_alternation(stages)
        return cls(stages=stages)

    @classmethod
    def implicit(cls, reader_name: str, writer_name: str) -> "PipelineSpec":
        """Pipeline a 2 stage costruita da --from/--to (o dalla
        risoluzione standard reader/writer) — il caso comune, che
        resta comunque solo una pipeline come tutte le altre."""
        stages: list[Stage] = [ReaderStage(name=reader_name), WriterStage(name=writer_name)]
        validate_alternation(stages)
        return cls(stages=stages)

    def cache_signature(self) -> str:
        """Rappresentazione stabile e ordinata per la cache key —
        cambiare anche un solo stage cambia la firma."""
        return self.signature_prefix(len(self.stages) - 1)

    def signature_prefix(self, upto_index: int) -> str:
        """Firma dei soli stage [0, upto_index] — usata per la cache
        per singolo stage: due pipeline che condividono lo stesso
        prefisso fino a un certo indice possono riusare l'output
        cachato di quel prefisso, anche se gli stage successivi
        differiscono."""
        parts = []
        for s in self.stages[: upto_index + 1]:
            if isinstance(s, ReaderStage):
                parts.append(f"reader:{s.name}")
            elif isinstance(s, WriterStage):
                parts.append(f"writer:{s.name}")
            else:
                parts.append(f"exec:{s.command}:{s.on_error}:{s.output_extension or ''}")
        return "|".join(parts)

    def reader_writer_pairs(self):
        """Ogni coppia (reader, writer) adiacente — per il check di
        compatibilità, applicato a OGNI coppia nella pipeline, non solo
        alla prima. Con un fan-out (reader seguito da più writer
        consecutivi), restituisce una coppia per CIASCUN writer del
        gruppo, non solo il primo — la compatibilità va verificata per
        ognuno."""
        n = len(self.stages)
        for i, stage in enumerate(self.stages):
            if isinstance(stage, ReaderStage):
                j = i + 1
                while j < n and isinstance(self.stages[j], WriterStage):
                    yield stage, self.stages[j]
                    j += 1

    def terminal_writer_start(self) -> int:
        """Indice del primo stage del gruppo finale di 'WriterStage'
        consecutivi (un gruppo di 1 o più — comprende anche il caso
        comune di un solo writer terminale). len(stages) se l'ultimo
        stage non è un writer (es. pipeline terminata da un 'exec')."""
        i = len(self.stages)
        while i > 0 and isinstance(self.stages[i - 1], WriterStage):
            i -= 1
        return i
