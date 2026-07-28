import stat
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agency.agents.rnd_agent import RnDAgent
from agency.config import Settings

STACK_DESCRIPTION = (
    "- orchestration: LangGraph (StateGraph + SQLite checkpointer)\n"
    "- inference: Ollama (local-first) with OpenRouter as fallback\n"
    "- image/video/audio generation: ComfyUI\n"
    "- social publishing: Postiz\n"
    "- video assembly: ffmpeg (StudioWorker)\n"
    "- document knowledge source: Paperless-ngx (OCR'd content, per-brand tag-scoped)"
)


@dataclass
class HealthCheckResult:
    name: str
    ok: bool
    detail: str


class DevOps:
    """Owns pipeline health, backups, and basic operational security checks.

    Reports to the human operator -- via whatever surfaces run_all_checks'
    output (CLI today; a real notification channel is a roadmap item, not
    fabricated here). Can consult RnDAgent for tooling suggestions, but
    doesn't do so automatically -- that's a deliberate call, not a gap.
    """

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None):
        self._settings = settings
        self._client = http_client or httpx.Client(timeout=5.0)

    def check_service_health(self) -> list[HealthCheckResult]:
        targets = {
            "postiz": self._settings.postiz_base_url,
            "ollama": self._settings.ollama_base_url,
            "comfyui": self._settings.comfyui_base_url,
            "paperless": self._settings.paperless_base_url,
        }
        results = []
        for name, url in targets.items():
            try:
                response = self._client.get(url)
                results.append(HealthCheckResult(name=name, ok=response.status_code < 500, detail=f"HTTP {response.status_code}"))
            except httpx.HTTPError as exc:
                results.append(HealthCheckResult(name=name, ok=False, detail=str(exc)))
        return results

    def check_env_file_permissions(self, env_path: str | Path = ".env") -> HealthCheckResult:
        path = Path(env_path)
        if not path.exists():
            return HealthCheckResult(name="env_permissions", ok=True, detail=f"{path} does not exist")
        mode = path.stat().st_mode
        world_readable = bool(mode & stat.S_IROTH)
        detail = f"{path} is {'world-readable' if world_readable else 'not world-readable'} (mode {oct(mode)[-3:]})"
        return HealthCheckResult(name="env_permissions", ok=not world_readable, detail=detail)

    def backup(self, roots: list[str | Path], backup_dir: str | Path) -> Path:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_path = backup_dir / f"backup-{timestamp}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            for root in roots:
                root_path = Path(root)
                if root_path.exists():
                    tar.add(root_path, arcname=root_path.name)
        return archive_path

    def consult_rnd(self, rnd: RnDAgent, focus: str | None = None) -> str:
        health = self.check_service_health()
        stack = STACK_DESCRIPTION + "\n" + "\n".join(
            f"- {r.name}: {'up' if r.ok else 'down'} ({r.detail})" for r in health
        )
        return rnd.suggest_improvements(stack, focus=focus)

    def close(self) -> None:
        self._client.close()
