"""
Pipeline specification and validation. See src/payload/docs/PIPELINE.md for the
full design and the reasoning behind the alternation rules.

Three stage types:
- ReaderStage: file -> in-memory data (TableIR)
- WriterStage: in-memory data -> file
- ExecStage: file -> file (external command)

Alternation rules, checked BEFORE running any stage:
1. the first stage must be a reader
2. a reader must be immediately followed by a writer
3. after a writer: reader, exec, another writer, or end of pipeline
4. after an exec: reader, exec, or end of pipeline
5. at least 2 stages (reader + writer)
6. fan-out: a reader can be followed by SEVERAL consecutive writers
   (all fed from the same IR, parsed only once) — but a group of 2+
   consecutive writers must be the last thing in the pipeline: no
   reader/exec can come after a fan-out. A group of a single writer
   has no such restriction (the usual behavior: reader/exec can follow
   it). See src/payload/docs/PIPELINE.md, Fan-out section.
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
    # required ONLY if this exec is the pipeline's last stage (needed
    # to determine the final output file's extension, which for
    # reader/writer is read from writer.extension)
    output_extension: str | None = None


Stage = Union[ReaderStage, WriterStage, ExecStage]


def parse_stage(raw: dict, index: int) -> Stage:
    if not isinstance(raw, dict):
        raise InvalidPipelineError(index, "each stage must be a TOML table, e.g. { type = \"reader\", name = \"...\" }")

    stage_type = raw.get("type")

    if stage_type == "reader":
        name = raw.get("name")
        if not name:
            raise InvalidPipelineError(index, "'reader' stage requires 'name'")
        return ReaderStage(name=name)

    if stage_type == "writer":
        name = raw.get("name")
        if not name:
            raise InvalidPipelineError(index, "'writer' stage requires 'name'")
        return WriterStage(name=name)

    if stage_type == "exec":
        command = raw.get("command")
        if not command:
            raise InvalidPipelineError(index, "'exec' stage requires 'command'")
        on_error = raw.get("on_error", "fail")
        if on_error not in VALID_ON_ERROR:
            raise InvalidPipelineError(
                index, f"'on_error' must be 'fail' or 'warn', got '{on_error}'"
            )
        return ExecStage(
            command=command,
            on_error=on_error,
            output_extension=raw.get("output_extension"),
        )

    raise InvalidPipelineError(
        index, f"unknown stage type: '{stage_type}' (expected: reader | writer | exec)"
    )


def validate_alternation(stages: list[Stage]) -> None:
    if len(stages) < 2:
        raise InvalidPipelineError(0, "the pipeline must have at least 2 stages (a reader and a writer)")

    if not isinstance(stages[0], ReaderStage):
        raise InvalidPipelineError(0, "the first stage must be a 'reader'")

    n = len(stages)
    i = 0
    while i < n:
        stage = stages[i]
        if isinstance(stage, ReaderStage):
            if i + 1 >= n:
                raise InvalidPipelineError(i, "a 'reader' can't be the pipeline's last stage")
            if not isinstance(stages[i + 1], WriterStage):
                raise InvalidPipelineError(
                    i, "a 'reader' must be immediately followed by a 'writer'"
                )
            i += 1
        elif isinstance(stage, WriterStage):
            run_start = i
            while i < n and isinstance(stages[i], WriterStage):
                i += 1
            run_end = i - 1
            # a writer name must be unique WITHIN a fan-out group: two
            # consecutive stages with the same writer would collide on
            # the same output file (fan-out means several DIFFERENT
            # writers). Two writers separated by a reader are two
            # separate read→write pairs and MAY reuse a writer.
            seen: dict[str, int] = {}
            for k in range(run_start, run_end + 1):
                name = stages[k].name
                if name in seen:
                    raise InvalidPipelineError(
                        k, f"writer '{name}' is already used in this fan-out group (stage #{seen[name]}) — a fan-out needs different writers"
                    )
                seen[name] = k
            if (run_end - run_start + 1) >= 2 and run_end != n - 1:
                raise InvalidPipelineError(
                    run_end,
                    "a group of several consecutive 'writer' stages (fan-out) must be the "
                    "pipeline's last group — no 'reader'/'exec' can follow it",
                )
        else:  # ExecStage
            i += 1

    last = stages[-1]
    if isinstance(last, ExecStage) and not last.output_extension:
        raise InvalidPipelineError(
            len(stages) - 1,
            "the last 'exec' stage of a pipeline must declare 'output_extension' "
            "(needed to determine the final file's name, e.g. output_extension = \".signed.bin\")",
        )


@dataclass
class PipelineSpec:
    stages: list[Stage] = field(default_factory=list)

    @classmethod
    def from_raw_stages(cls, raw_stages: list) -> "PipelineSpec":
        if not isinstance(raw_stages, list):
            raise InvalidPipelineError(0, "'pipeline.stages' must be a list of stages")
        stages = [parse_stage(raw, i) for i, raw in enumerate(raw_stages)]
        validate_alternation(stages)
        return cls(stages=stages)

    @classmethod
    def implicit(cls, reader_name: str, writer_name: str) -> "PipelineSpec":
        """2-stage pipeline built from --from/--to (or from the
        standard reader/writer resolution) — the common case, which is
        still just a pipeline like any other."""
        stages: list[Stage] = [ReaderStage(name=reader_name), WriterStage(name=writer_name)]
        validate_alternation(stages)
        return cls(stages=stages)

    def cache_signature(self) -> str:
        """Stable, ordered representation for the cache key — changing
        even a single stage changes the signature."""
        return self.signature_prefix(len(self.stages) - 1)

    def signature_prefix(self, upto_index: int) -> str:
        """Signature of just the stages [0, upto_index] — used for
        per-stage caching: two pipelines that share the same prefix up
        to a given index can reuse the cached output of that prefix,
        even if the later stages differ."""
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
        """Every adjacent (reader, writer) pair — for the
        compatibility check, applied to EVERY pair in the pipeline,
        not just the first. With a fan-out (reader followed by several
        consecutive writers), yields one pair per writer in the group,
        not just the first — compatibility must be checked for each of
        them."""
        n = len(self.stages)
        for i, stage in enumerate(self.stages):
            if isinstance(stage, ReaderStage):
                j = i + 1
                while j < n and isinstance(self.stages[j], WriterStage):
                    yield stage, self.stages[j]
                    j += 1

    def terminal_writer_start(self) -> int:
        """Index of the first stage of the final group of consecutive
        'WriterStage's (a group of 1 or more — this also covers the
        common case of a single terminal writer). len(stages) if the
        last stage isn't a writer (e.g. a pipeline ending in an
        'exec')."""
        i = len(self.stages)
        while i > 0 and isinstance(self.stages[i - 1], WriterStage):
            i -= 1
        return i
