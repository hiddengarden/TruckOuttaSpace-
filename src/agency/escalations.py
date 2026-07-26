import json
from pathlib import Path


class EscalationRegistry:
    """The Director's inbox: paused graph threads awaiting a human decision.

    One file for the whole agency (not per-brand) so a human has a single
    place to check, matching "Director... first point of escalation... to a
    human" -- there's one inbox, not one per customer/brand.
    """

    def __init__(self, state_root: str | Path):
        self._path = Path(state_root) / "escalations.json"

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())

    def _save(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(entries, indent=2))

    def add(self, thread_id: str, **details) -> None:
        entries = [e for e in self._load() if e["thread_id"] != thread_id]
        entries.append({"thread_id": thread_id, **details})
        self._save(entries)

    def remove(self, thread_id: str) -> None:
        self._save([e for e in self._load() if e["thread_id"] != thread_id])

    def get(self, thread_id: str) -> dict | None:
        return next((e for e in self._load() if e["thread_id"] == thread_id), None)

    def list(self) -> list[dict]:
        return self._load()
