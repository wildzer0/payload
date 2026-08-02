"""Lightweight append-only event log for the project (stored in
'.payload_activity/events.jsonl'): builds, commits, golden changes and
file-browser mutations, so the webapp can show a global timeline across
the whole project (page /log). Best-effort by design: a failing event
write must never break the operation that caused it."""
from __future__ import annotations

import json
import time
from pathlib import Path

ACTIVITY_DIRNAME = ".payload_activity"


def log_event(root: Path, kind: str, detail: str, level: str = "info") -> None:
    try:
        d = root / ACTIVITY_DIRNAME
        d.mkdir(exist_ok=True)
        with (d / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": time.time(), "kind": kind, "detail": detail, "level": level},
                ensure_ascii=False,
            ) + "\n")
    except OSError:  # pragma: no cover - defensive: log must never break the caller
        pass


def read_events(root: Path, limit: int = 100, offset: int = 0) -> dict:
    try:
        lines = (root / ACTIVITY_DIRNAME / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"events": [], "total": 0}
    total = len(lines)
    events: list[dict] = []
    # newest first, then the page slice — slicing the reversed list, not
    # the chronological one (a slice-then-reverse would return the OLDEST
    # events instead of the newest)
    for line in lines[::-1][offset:offset + limit]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # pragma: no cover - a torn line from a crash
            continue
    return {"events": events, "total": total}
