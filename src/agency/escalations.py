from __future__ import annotations

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

    def list_verified(self, checkpointer) -> list[dict]:
        """This JSON file is a cache, not the source of truth -- a human
        could resolve a thread by calling the compose graph directly (or a
        crash could leave a stale entry behind), and the file would never
        know. An entry is only genuinely still pending if its thread's
        compose-graph checkpoint is still parked at the escalate interrupt
        (`snapshot.next == ("escalate",)`, verified empirically: it's `()`
        both when a thread has been resumed and when the thread_id never
        existed at all, so either case is correctly treated as resolved).
        Anything else found here is pruned rather than shown as pending.

        Building a graph with placeholder agents is safe purely for
        get_state(): it only reads the checkpoint, it never executes a
        node, so content_agent/supervisor_agent are never actually called.
        """
        from agency.graph import build_compose_graph

        graph = build_compose_graph(object(), object()).compile(checkpointer=checkpointer)
        entries = self._load()
        verified, stale_ids = [], []
        for entry in entries:
            config = {"configurable": {"thread_id": entry["thread_id"]}}
            snapshot = graph.get_state(config)
            if "escalate" in snapshot.next:
                verified.append(entry)
            else:
                stale_ids.append(entry["thread_id"])
        if stale_ids:
            self._save([e for e in entries if e["thread_id"] not in stale_ids])
        return verified

    def get_verified(self, thread_id: str, checkpointer) -> dict | None:
        return next((e for e in self.list_verified(checkpointer) if e["thread_id"] == thread_id), None)
