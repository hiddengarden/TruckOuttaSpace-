import json
from pathlib import Path

_MAX_HISTORY = 200


class TopicHistory:
    """Tracks recently used topics per customer/brand/project so scheduled runs don't repeat."""

    def __init__(self, state_root: str | Path, customer_slug: str, brand_slug: str, project_slug: str | None):
        self._path = (
            Path(state_root) / customer_slug / brand_slug / (project_slug or "_default") / "topic_history.json"
        )

    def recent(self, limit: int = 20) -> list[str]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())[-limit:]

    def record(self, topic: str) -> None:
        history = json.loads(self._path.read_text()) if self._path.exists() else []
        history.append(topic)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(history[-_MAX_HISTORY:]))
