from __future__ import annotations

import httpx


class TelegramNotifier:
    """The Director's channel to the human: escalations, daily progress, and
    monetization summaries -- not general ops noise (that goes through
    EmailTicketNotifier's tickets instead). Wraps Telegram's public Bot API
    (`POST https://api.telegram.org/bot<token>/sendMessage`, `chat_id` +
    `text`), per Telegram's own docs (core.telegram.org/bots/api).
    """

    def __init__(self, bot_token: str, chat_id: str, http_client: httpx.Client | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = http_client or httpx.Client(timeout=10.0)

    def send(self, text: str) -> dict:
        response = self._client.post(
            f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
            json={"chat_id": self._chat_id, "text": text},
        )
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
