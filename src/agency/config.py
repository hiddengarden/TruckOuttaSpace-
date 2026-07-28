import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    postiz_base_url: str
    postiz_api_key: str
    ollama_base_url: str
    ollama_model: str
    openrouter_base_url: str
    openrouter_api_key: str
    openrouter_model: str
    knowledge_root: str
    state_root: str
    org_dir: str
    checkpoint_db_path: str
    run_interval_seconds: int
    comfyui_base_url: str
    workflows_dir: str
    video_workflows_dir: str
    audio_workflows_dir: str
    assets_root: str
    content_root: str
    telegram_bot_token: str
    telegram_chat_id: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_addr: str
    smtp_to_addr: str
    smtp_use_tls: bool
    paperless_base_url: str
    paperless_api_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            postiz_base_url=os.environ.get("POSTIZ_BASE_URL", "http://localhost:3000"),
            postiz_api_key=os.environ.get("POSTIZ_API_KEY", ""),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
            openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            openrouter_model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
            knowledge_root=os.environ.get("KNOWLEDGE_ROOT", "./knowledge"),
            state_root=os.environ.get("STATE_ROOT", "./state"),
            org_dir=os.environ.get("ORG_DIR", "./org"),
            checkpoint_db_path=os.environ.get("CHECKPOINT_DB_PATH", "./state/checkpoints.db"),
            run_interval_seconds=int(os.environ.get("RUN_INTERVAL_SECONDS", "21600")),
            comfyui_base_url=os.environ.get("COMFYUI_BASE_URL", "http://localhost:8188"),
            workflows_dir=os.environ.get("WORKFLOWS_DIR", "./workflows/image"),
            video_workflows_dir=os.environ.get("VIDEO_WORKFLOWS_DIR", "./workflows/video"),
            audio_workflows_dir=os.environ.get("AUDIO_WORKFLOWS_DIR", "./workflows/audio"),
            assets_root=os.environ.get("ASSETS_ROOT", "./assets"),
            content_root=os.environ.get("CONTENT_ROOT", "./content"),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_from_addr=os.environ.get("SMTP_FROM_ADDR", ""),
            smtp_to_addr=os.environ.get("SMTP_TO_ADDR", ""),
            smtp_use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no"),
            paperless_base_url=os.environ.get("PAPERLESS_BASE_URL", "http://localhost:8010"),
            paperless_api_token=os.environ.get("PAPERLESS_API_TOKEN", ""),
        )
