from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agency.filelock import locked


@dataclass
class Ticket:
    ticket_id: str
    dedup_key: str
    category: str
    severity: str
    summary: str
    detail: dict = field(default_factory=dict)
    status: str = "open"  # "open" | "resolved"
    occurrences: int = 1
    created_at: str = ""
    updated_at: str = ""
    message_id: str | None = None


class TicketRegistry:
    """A lightweight, self-hosted stand-in for a real ticketing system: one
    JSON file, one entry per distinct problem (`dedup_key`), bumped in place
    on repeat occurrences instead of spawning a new ticket every time the
    same thing goes wrong (e.g. a brand's local model being unreachable on
    every run in a bad week shouldn't be 50 separate tickets). No real
    ticketing SaaS was named, so this -- paired with EmailTicketNotifier's
    threaded emails -- is the honest scope: a ticket *index*, not a fabricated
    integration with a vendor that was never specified.
    """

    def __init__(self, state_root: str | Path):
        self._path = Path(state_root) / "tickets.json"

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())

    def _save(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(entries, indent=2))

    def open_or_bump(self, dedup_key: str, category: str, severity: str, summary: str, detail: dict) -> tuple[Ticket, bool]:
        """Returns (ticket, is_new). is_new tells the caller whether to send
        a fresh notification email or a threaded follow-up."""
        with locked(self._path):
            entries = self._load()
            now = datetime.now(timezone.utc).isoformat()
            existing = next((e for e in entries if e["dedup_key"] == dedup_key and e["status"] == "open"), None)
            if existing is not None:
                existing["occurrences"] += 1
                existing["updated_at"] = now
                existing["summary"] = summary
                existing["detail"] = detail
                existing["severity"] = severity
                self._save(entries)
                return Ticket(**existing), False

            new_entry = {
                "ticket_id": uuid4().hex[:10],
                "dedup_key": dedup_key,
                "category": category,
                "severity": severity,
                "summary": summary,
                "detail": detail,
                "status": "open",
                "occurrences": 1,
                "created_at": now,
                "updated_at": now,
                "message_id": None,
            }
            entries.append(new_entry)
            self._save(entries)
            return Ticket(**new_entry), True

    def set_message_id(self, ticket_id: str, message_id: str) -> None:
        with locked(self._path):
            entries = self._load()
            for entry in entries:
                if entry["ticket_id"] == ticket_id:
                    entry["message_id"] = message_id
            self._save(entries)

    def resolve(self, dedup_key: str) -> None:
        with locked(self._path):
            entries = self._load()
            for entry in entries:
                if entry["dedup_key"] == dedup_key and entry["status"] == "open":
                    entry["status"] = "resolved"
                    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save(entries)

    def list_open(self) -> list[Ticket]:
        return [Ticket(**e) for e in self._load() if e["status"] == "open"]
