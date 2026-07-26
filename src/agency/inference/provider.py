import base64
from typing import Protocol

import httpx

from agency.config import Settings


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...
    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str: ...


class OpenAICompatProvider:
    """Talks to any OpenAI-compatible /chat/completions endpoint (Ollama, OpenRouter, ...).

    complete_with_image uses the standard OpenAI vision content-parts shape
    (`{"type": "image_url", "image_url": {"url": "data:...;base64,..."}}`),
    per OpenAI's own spec and Ollama's official OpenAI-compatibility docs --
    it requires a vision-capable model (e.g. Ollama's llava) configured on
    whichever endpoint receives it.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        return self._post_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return self._post_chat(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                    ],
                },
            ]
        )

    def _post_chat(self, messages: list[dict]) -> str:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            timeout=self._timeout,
            json={"model": self._model, "messages": messages},
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


class LocalFirstProvider:
    """Tries a local provider first; falls back to a remote one on connection/timeout errors."""

    def __init__(self, local: LLMProvider, fallback: LLMProvider):
        self._local = local
        self._fallback = fallback

    def complete(self, system: str, user: str) -> str:
        try:
            return self._local.complete(system, user)
        except (httpx.ConnectError, httpx.TimeoutException):
            return self._fallback.complete(system, user)

    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str:
        try:
            return self._local.complete_with_image(system, user, image_bytes, mime_type)
        except (httpx.ConnectError, httpx.TimeoutException):
            return self._fallback.complete_with_image(system, user, image_bytes, mime_type)


def default_provider(settings: Settings) -> LLMProvider:
    local = OpenAICompatProvider(settings.ollama_base_url, settings.ollama_model)
    fallback = OpenAICompatProvider(
        settings.openrouter_base_url, settings.openrouter_model, api_key=settings.openrouter_api_key
    )
    return LocalFirstProvider(local, fallback)
