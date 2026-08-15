from typing import Protocol

import httpx

from agency.inference.provider import settings_base_without_v1


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class OllamaEmbeddingProvider:
    """Local-only, no cloud fallback -- unlike LocalFirstProvider/generation,
    there's no reason to send raw corpus content to a cloud provider just to
    compute a retrieval embedding, and Ollama's embedding models are small
    enough that "unavailable" should just mean KnowledgeBase falls back to
    keyword search (see knowledge.py), not a LocalInferenceUnavailable-style
    hard stop.

    Verified against Ollama's own docs: POST /api/embed is the current,
    non-deprecated endpoint (/api/embeddings, singular, is legacy) --
    request {"model": ..., "input": <str or list[str]>}, response
    {"embeddings": [[...], ...]} (one vector per input). We send one input
    per call, so index [0].
    """

    def __init__(self, ollama_base_url: str, model: str, http_client: httpx.Client | None = None, timeout: float = 30.0):
        self._native_base = settings_base_without_v1(ollama_base_url)
        self._model = model
        self._client = http_client or httpx.Client(timeout=timeout)

    def embed(self, text: str) -> list[float]:
        response = self._client.post(
            f"{self._native_base}/api/embed", json={"model": self._model, "input": text}
        )
        response.raise_for_status()
        return response.json()["embeddings"][0]

    def close(self) -> None:
        self._client.close()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
