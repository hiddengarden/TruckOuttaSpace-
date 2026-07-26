import json

import httpx
import respx

from agency.notifications.telegram import TelegramNotifier


@respx.mock
def test_send_posts_chat_id_and_text_to_the_real_bot_api_shape():
    route = respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )
    notifier = TelegramNotifier("123:ABC", "chat-1")

    result = notifier.send("hello director")

    assert result == {"ok": True, "result": {"message_id": 1}}
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body == {"chat_id": "chat-1", "text": "hello director"}


@respx.mock
def test_send_raises_on_http_error():
    respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(return_value=httpx.Response(401))
    notifier = TelegramNotifier("123:ABC", "chat-1")

    try:
        notifier.send("hello")
        assert False, "expected an HTTPStatusError"
    except httpx.HTTPStatusError:
        pass
