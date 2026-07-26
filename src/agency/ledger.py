from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class RunLedger:
    """Append-only JSONL log of what actually happened on every run -- which
    provider answered (local vs. cloud fallback), parse successes/failures,
    revision counts, publish attempts/results. Exists because a scheduled,
    unattended system with only best-effort JSON parsing and a silent
    local-first/fallback split is otherwise undiagnosable after the fact:
    by the time a quality problem surfaces, the only evidence of what the
    system actually did is gone unless it was recorded when it happened.

    `on_event`, if given, is called with (event, fields) every time an event
    is recorded -- this is the single point Director's notifications hook
    into, so run.py/graph.py never need to know notifications exist at all.
    """

    def __init__(self, state_root: str | Path, on_event: Callable[[str, dict], None] | None = None):
        self._path = Path(state_root) / "run_ledger.jsonl"
        self._on_event = on_event

    def record(self, event: str, **fields: Any) -> None:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if self._on_event is not None:
            self._on_event(event, fields)

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines() if line.strip()]
