import mimetypes
from pathlib import Path
from typing import Any, Literal

import httpx

PostStatus = Literal["draft", "publish", "pending", "future"]


class WordPressClient:
    """Thin wrapper over WordPress core's REST API (wp-json/wp/v2) -- a
    website-publishing channel alongside/instead of Postiz's social
    channels. Chosen over Elxis: Elxis's own docs describe its "REST API"
    as a content *source* for its microblog module (pulling external feeds
    in), not a documented endpoint for creating articles programmatically;
    no create/publish API surface for it could be found. WordPress's REST
    API is core, stable since 4.7, with Application Passwords (core since
    5.6) as the standard non-deprecated auth method for external apps.

    Auth: HTTP Basic with a username + Application Password -- generate one
    under Users -> Profile -> Application Passwords in the WP admin.
    WordPress itself blocks Application Passwords over plain HTTP unless
    explicitly allowed for local/dev use; use HTTPS in production.
    """

    def __init__(self, base_url: str, username: str, app_password: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/wp-json/wp/v2",
            auth=(username, app_password),
            timeout=timeout,
        )

    def create_post(
        self,
        *,
        title: str,
        content: str,
        status: PostStatus = "draft",
        excerpt: str = "",
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        featured_media: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "content": content, "status": status}
        if excerpt:
            body["excerpt"] = excerpt
        if categories:
            body["categories"] = categories
        if tags:
            body["tags"] = tags
        if featured_media is not None:
            body["featured_media"] = featured_media
        response = self._client.post("/posts", json=body)
        response.raise_for_status()
        return response.json()

    def upload_media(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self._client.post(
            "/media",
            content=path.read_bytes(),
            headers={"Content-Disposition": f'attachment; filename="{path.name}"', "Content-Type": mime_type},
        )
        response.raise_for_status()
        return response.json()

    def list_categories(self) -> list[dict[str, Any]]:
        response = self._client.get("/categories", params={"per_page": 100})
        response.raise_for_status()
        return response.json()

    def list_tags(self) -> list[dict[str, Any]]:
        response = self._client.get("/tags", params={"per_page": 100})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
