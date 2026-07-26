import base64
import json

import httpx
import pytest
import respx

from agency.inference.provider import (
    LocalFirstProvider,
    LocalInferenceUnavailable,
    OpenAICompatProvider,
    settings_base_without_v1,
    unload_ollama,
)


@respx.mock
def test_complete_posts_plain_text_messages():
    route = respx.post("http://llm.local/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
    )

    result = OpenAICompatProvider("http://llm.local", "test-model").complete("sys", "usr")

    assert result == "hello"
    body = json.loads(route.calls[0].request.content)
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]


@respx.mock
def test_complete_with_image_sends_data_uri_content_parts():
    route = respx.post("http://llm.local/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "looks good"}}]})
    )

    result = OpenAICompatProvider("http://llm.local", "vision-model").complete_with_image(
        "sys", "review this", b"fake-bytes", "image/png"
    )

    assert result == "looks good"
    body = json.loads(route.calls[0].request.content)
    image_part = body["messages"][1]["content"][1]
    assert image_part["type"] == "image_url"
    expected_b64 = base64.b64encode(b"fake-bytes").decode("ascii")
    assert image_part["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"


@respx.mock
def test_local_first_provider_raises_by_default_instead_of_falling_back():
    respx.post("http://local.invalid/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    provider = LocalFirstProvider(
        OpenAICompatProvider("http://local.invalid", "local-model"),
        OpenAICompatProvider("http://fallback.local", "fallback-model"),
    )

    with pytest.raises(LocalInferenceUnavailable):
        provider.complete("sys", "usr")


@respx.mock
def test_local_first_provider_falls_back_when_allowed():
    respx.post("http://local.invalid/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    respx.post("http://fallback.local/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "fallback answer"}}]})
    )

    provider = LocalFirstProvider(
        OpenAICompatProvider("http://local.invalid", "local-model"),
        OpenAICompatProvider("http://fallback.local", "fallback-model"),
        allow_fallback=True,
    )

    assert provider.complete_with_image("sys", "usr", b"x", "image/png") == "fallback answer"


@respx.mock
def test_local_first_provider_calls_on_fallback_hook():
    respx.post("http://local.invalid/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    respx.post("http://fallback.local/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "fallback answer"}}]})
    )
    seen = []

    provider = LocalFirstProvider(
        OpenAICompatProvider("http://local.invalid", "local-model"),
        OpenAICompatProvider("http://fallback.local", "fallback-model"),
        allow_fallback=True,
        on_fallback=seen.append,
    )

    provider.complete("sys", "usr")

    assert len(seen) == 1
    assert "refused" in seen[0]


@respx.mock
def test_local_first_provider_success_does_not_trigger_fallback_hook():
    respx.post("http://local.invalid/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "local answer"}}]})
    )
    seen = []

    provider = LocalFirstProvider(
        OpenAICompatProvider("http://local.invalid", "local-model"),
        OpenAICompatProvider("http://fallback.local", "fallback-model"),
        allow_fallback=True,
        on_fallback=seen.append,
    )

    assert provider.complete("sys", "usr") == "local answer"
    assert seen == []


def test_settings_base_without_v1_strips_suffix():
    assert settings_base_without_v1("http://localhost:11434/v1") == "http://localhost:11434"
    assert settings_base_without_v1("http://localhost:11434/v1/") == "http://localhost:11434"
    assert settings_base_without_v1("http://localhost:11434") == "http://localhost:11434"


@respx.mock
def test_unload_ollama_posts_keep_alive_zero_to_native_endpoint():
    route = respx.post("http://localhost:11434/api/generate").mock(return_value=httpx.Response(200, json={}))

    unload_ollama("http://localhost:11434/v1", "llama3.1")

    body = json.loads(route.calls[0].request.content)
    assert body == {"model": "llama3.1", "keep_alive": 0}


@respx.mock
def test_unload_ollama_swallows_connection_errors():
    respx.post("http://localhost:11434/api/generate").mock(side_effect=httpx.ConnectError("refused"))

    unload_ollama("http://localhost:11434/v1", "llama3.1")  # must not raise
