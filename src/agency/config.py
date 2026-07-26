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
    brands_dir: str
    run_interval_seconds: int

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
            brands_dir=os.environ.get("BRANDS_DIR", "./brands"),
            run_interval_seconds=int(os.environ.get("RUN_INTERVAL_SECONDS", "21600")),
        )
