import mimetypes
from pathlib import Path
from typing import Any, Literal

import httpx

PostType = Literal["draft", "schedule", "now", "update"]


class PostizClient:
    """Thin wrapper over Postiz's public API (mounted at /public/v1).

    Auth: the raw API key is sent as the "Authorization" header (no "Bearer"
    prefix) -- see apps/backend/src/services/auth/public.auth.middleware.ts
    in gitroomhq/postiz-app.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/public/v1",
            headers={"Authorization": api_key},
            timeout=timeout,
        )

    def list_groups(self) -> list[dict[str, Any]]:
        response = self._client.get("/groups")
        response.raise_for_status()
        return response.json()

    def list_integrations(self, group: str | None = None) -> list[dict[str, Any]]:
        params = {"group": group} if group else None
        response = self._client.get("/integrations", params=params)
        response.raise_for_status()
        return response.json()

    def upload_media(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self._client.post("/upload", files={"file": (path.name, path.read_bytes(), mime_type)})
        response.raise_for_status()
        return response.json()

    def create_post(
        self,
        *,
        post_type: PostType,
        date_iso: str,
        integration_ids: list[str],
        content: str,
        group: str | None = None,
        short_link: bool = False,
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # images: MediaDto refs, i.e. {"id": ..., "path": ...} dicts as returned
        # by upload_media() -- see libraries/nestjs-libraries/.../media.dto.ts.
        body = {
            "type": post_type,
            "shortLink": short_link,
            "date": date_iso,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [{"content": content, "image": images or []}],
                    **({"group": group} if group else {}),
                }
                for integration_id in integration_ids
            ],
        }
        response = self._client.post("/posts", json=body)
        response.raise_for_status()
        return response.json()

    def change_post_status(self, post_id: str, status: str) -> dict[str, Any]:
        response = self._client.put(f"/posts/{post_id}/status", json={"status": status})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
