import base64
import json

import httpx
import pytest
import respx

from agency.inference.provider import LocalFirstProvider, OpenAICompatProvider


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
def test_local_first_provider_falls_back_on_connect_error_for_vision_too():
    respx.post("http://local.invalid/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    respx.post("http://fallback.local/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "fallback answer"}}]})
    )

    provider = LocalFirstProvider(
        OpenAICompatProvider("http://local.invalid", "local-model"),
        OpenAICompatProvider("http://fallback.local", "fallback-model"),
    )

    assert provider.complete_with_image("sys", "usr", b"x", "image/png") == "fallback answer"
