import tarfile

import httpx
import respx

from agency.agents.devops import DevOps
from agency.agents.rnd_agent import RnDAgent
from agency.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        postiz_base_url="http://postiz.local",
        postiz_api_key="k",
        ollama_base_url="http://ollama.local",
        ollama_model="m",
        openrouter_base_url="http://openrouter.local",
        openrouter_api_key="",
        openrouter_model="m",
        knowledge_root="k",
        state_root="s",
        org_dir="o",
        checkpoint_db_path="c",
        run_interval_seconds=1,
        comfyui_base_url="http://comfy.local",
        workflows_dir="w",
        video_workflows_dir="v",
        audio_workflows_dir="a",
        assets_root="assets",
        content_root="content",
        telegram_bot_token="",
        telegram_chat_id="",
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from_addr="",
        smtp_to_addr="",
        smtp_use_tls=True,
        paperless_base_url="http://paperless.local",
        paperless_api_token="",
    )
    base.update(overrides)
    return Settings(**base)


@respx.mock
def test_check_service_health_reports_up_and_down():
    respx.get("http://postiz.local").mock(return_value=httpx.Response(200))
    respx.get("http://ollama.local").mock(return_value=httpx.Response(200))
    respx.get("http://comfy.local").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("http://paperless.local").mock(return_value=httpx.Response(200))

    results = {r.name: r for r in DevOps(_settings()).check_service_health()}

    assert results["postiz"].ok is True
    assert results["ollama"].ok is True
    assert results["comfyui"].ok is False
    assert "refused" in results["comfyui"].detail
    assert results["paperless"].ok is True


def test_check_env_file_permissions_flags_world_readable(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SECRET=1")
    env_path.chmod(0o644)

    result = DevOps(_settings()).check_env_file_permissions(env_path)

    assert result.ok is False


def test_check_env_file_permissions_ok_when_not_world_readable(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SECRET=1")
    env_path.chmod(0o600)

    result = DevOps(_settings()).check_env_file_permissions(env_path)

    assert result.ok is True


def test_check_env_file_permissions_ok_when_file_missing(tmp_path):
    result = DevOps(_settings()).check_env_file_permissions(tmp_path / "does-not-exist.env")

    assert result.ok is True


def test_backup_archives_given_roots(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "a.md").write_text("content")

    archive_path = DevOps(_settings()).backup([knowledge_dir], tmp_path / "backups")

    assert archive_path.exists()
    with tarfile.open(archive_path) as tar:
        names = tar.getnames()
    assert "knowledge/a.md" in names


def test_backup_skips_roots_that_do_not_exist(tmp_path):
    archive_path = DevOps(_settings()).backup([tmp_path / "missing"], tmp_path / "backups")

    with tarfile.open(archive_path) as tar:
        assert tar.getnames() == []


class RecordingProvider:
    def __init__(self, reply: str = "use tool X"):
        self.reply = reply
        self.last_system = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


@respx.mock
def test_consult_rnd_passes_health_results_into_stack_description():
    respx.get("http://postiz.local").mock(return_value=httpx.Response(200))
    respx.get("http://ollama.local").mock(return_value=httpx.Response(200))
    respx.get("http://comfy.local").mock(return_value=httpx.Response(200))
    respx.get("http://paperless.local").mock(return_value=httpx.Response(200))

    provider = RecordingProvider()
    result = DevOps(_settings()).consult_rnd(RnDAgent(provider), focus="video pipeline")

    assert result == "use tool X"
    assert "postiz: up" in provider.last_system
    assert "video pipeline" in provider.last_system
