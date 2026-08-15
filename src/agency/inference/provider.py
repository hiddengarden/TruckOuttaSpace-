import base64
from typing import Callable, Protocol

import httpx

from agency.config import Settings


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...
    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str: ...


class LocalInferenceUnavailable(Exception):
    """Raised instead of silently falling back to a cloud provider when a
    brand hasn't opted into cloud fallback (Brand.allow_cloud_fallback=False,
    the default) and the local endpoint is unreachable."""


class OpenAICompatProvider:
    """Talks to any OpenAI-compatible /chat/completions endpoint (Ollama, OpenRouter, ...).

    complete_with_image uses the standard OpenAI vision content-parts shape
    (`{"type": "image_url", "image_url": {"url": "data:...;base64,..."}}`).
    Verified against Ollama's actual request-parsing source
    (openai/openai.go's chat-completions handler), not just docs: Ollama's
    own examples show a bare-string `image_url`, but the parser explicitly
    accepts both that and this nested `{"url": ...}` form -- so this single
    shape works unmodified against both Ollama and OpenRouter (which, per
    its own docs, requires the nested form). Two real constraints confirmed
    from that same source, not assumed: only base64 data URIs work (an
    http(s) URL is explicitly rejected -- "please use base64 encoded data
    instead"), and only jpeg/jpg/png/webp mime prefixes are accepted -- gif
    is not (see designer.py's _MIME_BY_SUFFIX comment). Requires a
    vision-capable model (e.g. Ollama's llava/qwen3-vl) configured on
    whichever endpoint receives it -- this method has never been run
    against a live model in this codebase's own development, only
    respx-mocked; this docstring's grounding is source-level, not an
    end-to-end confirmation.
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
    """Tries a local provider first. On connection/timeout failure: raises
    LocalInferenceUnavailable if `allow_fallback` is False (the default a
    brand gets unless it opts in), otherwise falls back to the remote
    provider and calls `on_fallback(detail)` -- callers use that hook to log
    to the run ledger and notify, since a silent fallback is exactly the
    failure mode that makes an unattended scheduled run undiagnosable.
    """

    def __init__(
        self,
        local: LLMProvider,
        fallback: LLMProvider,
        allow_fallback: bool = False,
        on_fallback: Callable[[str], None] | None = None,
    ):
        self._local = local
        self._fallback = fallback
        self._allow_fallback = allow_fallback
        self._on_fallback = on_fallback

    def complete(self, system: str, user: str) -> str:
        try:
            return self._local.complete(system, user)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return self._fall_back(exc, lambda: self._fallback.complete(system, user))

    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str:
        try:
            return self._local.complete_with_image(system, user, image_bytes, mime_type)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return self._fall_back(
                exc, lambda: self._fallback.complete_with_image(system, user, image_bytes, mime_type)
            )

    def _fall_back(self, exc: Exception, call_fallback: Callable[[], str]) -> str:
        if not self._allow_fallback:
            raise LocalInferenceUnavailable(str(exc)) from exc
        if self._on_fallback is not None:
            self._on_fallback(str(exc))
        return call_fallback()


def default_provider(
    settings: Settings, allow_fallback: bool, on_fallback: Callable[[str], None] | None = None
) -> LLMProvider:
    local = OpenAICompatProvider(settings.ollama_base_url, settings.ollama_model)
    fallback = OpenAICompatProvider(
        settings.openrouter_base_url, settings.openrouter_model, api_key=settings.openrouter_api_key
    )
    return LocalFirstProvider(local, fallback, allow_fallback=allow_fallback, on_fallback=on_fallback)


def unload_ollama(ollama_base_url: str, model: str, timeout: float = 10.0) -> None:
    """Frees VRAM before a ComfyUI-heavy phase starts, via Ollama's native
    /api/generate with keep_alive=0 -- confirmed via Ollama's own FAQ; the
    OpenAI-compatible endpoint this provider otherwise uses does not
    reliably honor keep_alive. Best-effort: a failure here must not block
    the finalize phase, it only means VRAM contention isn't avoided this run.
    """
    native_base = settings_base_without_v1(ollama_base_url)
    try:
        httpx.post(f"{native_base}/api/generate", json={"model": model, "keep_alive": 0}, timeout=timeout)
    except httpx.HTTPError:
        pass


def settings_base_without_v1(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
