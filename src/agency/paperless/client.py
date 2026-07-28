from typing import Any

import httpx

MAX_PAGES = 20  # bounded pagination -- a runaway filter shouldn't pull an entire archive in one ingest


class PaperlessClient:
    """Thin wrapper over Paperless-ngx's REST API (`/api/`) -- the library
    of host materials (books, invoices, receipts, paperwork) becomes a
    knowledge source the same way a scraped URL or a local vault is.

    Auth: token-based, `Authorization: Token <token>` (per Paperless-ngx's
    own docs -- note this is "Token", not "Bearer"). Generate one from
    Paperless's "My Profile" UI, or `POST /api/token/` with credentials.
    """

    def __init__(self, base_url: str, api_token: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/api",
            headers={"Authorization": f"Token {api_token}"},
            timeout=timeout,
        )

    def list_documents(
        self,
        *,
        tag_id: int | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns full document objects (including `content`, the OCR'd
        text) for whatever filters are given -- at least one should
        normally be passed, since an unfiltered call would pull every
        document in the whole Paperless instance, not just what's relevant
        to one brand."""
        params: dict[str, Any] = {}
        if tag_id is not None:
            params["tags__id"] = tag_id
        if correspondent_id is not None:
            params["correspondent__id"] = correspondent_id
        if document_type_id is not None:
            params["document_type__id"] = document_type_id
        if query:
            params["query"] = query

        documents: list[dict[str, Any]] = []
        url = "/documents/"
        for _ in range(MAX_PAGES):
            response = self._client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            documents.extend(payload.get("results", []))
            next_url = payload.get("next")
            if not next_url:
                break
            url = next_url
            params = None  # `next` is already a full URL with query params baked in
        return documents

    def list_tags(self) -> list[dict[str, Any]]:
        response = self._client.get("/tags/", params={"page_size": 100})
        response.raise_for_status()
        return response.json().get("results", [])

    def list_correspondents(self) -> list[dict[str, Any]]:
        response = self._client.get("/correspondents/", params={"page_size": 100})
        response.raise_for_status()
        return response.json().get("results", [])

    def list_document_types(self) -> list[dict[str, Any]]:
        response = self._client.get("/document_types/", params={"page_size": 100})
        response.raise_for_status()
        return response.json().get("results", [])

    def find_tag_id(self, name: str) -> int | None:
        return next((t["id"] for t in self.list_tags() if t["name"].lower() == name.lower()), None)

    def close(self) -> None:
        self._client.close()
