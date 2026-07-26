import json

import httpx
import respx

from agency.postiz.client import PostizClient


@respx.mock
def test_upload_media_posts_multipart_file(tmp_path):
    route = respx.post("http://postiz.local/public/v1/upload").mock(
        return_value=httpx.Response(200, json={"id": "media_1", "path": "/uploads/cover.png"})
    )
    client = PostizClient("http://postiz.local", "secret-key")

    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake-png-bytes")
    result = client.upload_media(image_path)

    assert result == {"id": "media_1", "path": "/uploads/cover.png"}
    request = route.calls[0].request
    assert request.headers["authorization"] == "secret-key"
    assert b"cover.png" in request.content
    assert b"fake-png-bytes" in request.content


@respx.mock
def test_create_post_attaches_images():
    route = respx.post("http://postiz.local/public/v1/posts").mock(
        return_value=httpx.Response(200, json={"id": "post_1"})
    )
    client = PostizClient("http://postiz.local", "secret-key")

    client.create_post(
        post_type="draft",
        date_iso="2026-07-26T00:00:00+00:00",
        integration_ids=["int_1"],
        content="hello world",
        images=[{"id": "media_1", "path": "/uploads/cover.png"}],
    )

    payload = json.loads(route.calls[0].request.content)
    assert payload["posts"][0]["value"][0]["image"] == [{"id": "media_1", "path": "/uploads/cover.png"}]


@respx.mock
def test_create_post_sends_expected_shape():
    route = respx.post("http://postiz.local/public/v1/posts").mock(
        return_value=httpx.Response(200, json={"id": "post_1"})
    )
    client = PostizClient("http://postiz.local", "secret-key")

    result = client.create_post(
        post_type="draft",
        date_iso="2026-07-26T00:00:00+00:00",
        integration_ids=["int_1"],
        content="hello world",
        group="group_1",
    )

    assert result == {"id": "post_1"}
    import json

    request = route.calls[0].request
    assert request.headers["authorization"] == "secret-key"
    payload = json.loads(request.content)
    assert payload["type"] == "draft"
    assert payload["posts"][0]["integration"]["id"] == "int_1"
    assert payload["posts"][0]["value"][0]["content"] == "hello world"
    assert payload["posts"][0]["group"] == "group_1"


@respx.mock
def test_list_groups():
    respx.get("http://postiz.local/public/v1/groups").mock(
        return_value=httpx.Response(200, json=[{"id": "g1", "name": "Example Co"}])
    )
    client = PostizClient("http://postiz.local", "secret-key")

    assert client.list_groups() == [{"id": "g1", "name": "Example Co"}]
