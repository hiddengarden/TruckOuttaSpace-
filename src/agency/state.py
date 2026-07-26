import json
from pathlib import Path

from agency.brand import BrandProfile

_MAX_HISTORY = 200


class TopicHistory:
    """Tracks recently used topics per brand so scheduled runs don't repeat themselves."""

    def __init__(self, state_root: str | Path, brand: BrandProfile):
        self._path = Path(state_root) / brand.slug / "topic_history.json"

    def recent(self, limit: int = 20) -> list[str]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())[-limit:]

    def record(self, topic: str) -> None:
        history = json.loads(self._path.read_text()) if self._path.exists() else []
        history.append(topic)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(history[-_MAX_HISTORY:]))
