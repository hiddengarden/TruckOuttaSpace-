import json

import httpx
import pytest
import respx

from agency.inference.embeddings import OllamaEmbeddingProvider, cosine_similarity


@respx.mock
def test_embed_posts_to_native_api_embed_and_strips_v1_suffix():
    route = respx.post("http://ollama.local/api/embed").mock(
        return_value=httpx.Response(200, json={"model": "nomic-embed-text", "embeddings": [[0.1, 0.2, 0.3]]})
    )

    result = OllamaEmbeddingProvider("http://ollama.local/v1", "nomic-embed-text").embed("hello world")

    assert result == [0.1, 0.2, 0.3]
    body = json.loads(route.calls[0].request.content)
    assert body == {"model": "nomic-embed-text", "input": "hello world"}


@respx.mock
def test_embed_raises_on_http_error():
    respx.post("http://ollama.local/api/embed").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        OllamaEmbeddingProvider("http://ollama.local/v1", "nomic-embed-text").embed("x")


def test_cosine_similarity_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_handles_zero_vector_without_dividing_by_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
