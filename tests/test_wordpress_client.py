import base64
import json

import httpx
import respx

from agency.wordpress.client import WordPressClient


@respx.mock
def test_create_post_sends_basic_auth_and_expected_body():
    route = respx.post("http://wp.local/wp-json/wp/v2/posts").mock(
        return_value=httpx.Response(201, json={"id": 42, "link": "http://wp.local/?p=42"})
    )

    client = WordPressClient("http://wp.local", "editor", "app-pass-1234")
    result = client.create_post(
        title="Hello", content="<p>World</p>", status="draft", categories=[2], tags=[3, 4], featured_media=99
    )

    assert result == {"id": 42, "link": "http://wp.local/?p=42"}
    request = route.calls[0].request
    expected_auth = "Basic " + base64.b64encode(b"editor:app-pass-1234").decode()
    assert request.headers["authorization"] == expected_auth
    body = json.loads(request.content)
    assert body == {
        "title": "Hello",
        "content": "<p>World</p>",
        "status": "draft",
        "categories": [2],
        "tags": [3, 4],
        "featured_media": 99,
    }


@respx.mock
def test_create_post_omits_unset_optional_fields():
    route = respx.post("http://wp.local/wp-json/wp/v2/posts").mock(return_value=httpx.Response(201, json={"id": 1}))

    WordPressClient("http://wp.local", "editor", "pw").create_post(title="Hi", content="body")

    body = json.loads(route.calls[0].request.content)
    assert body == {"title": "Hi", "content": "body", "status": "draft"}


@respx.mock
def test_upload_media_sends_content_disposition_and_content_type(tmp_path):
    route = respx.post("http://wp.local/wp-json/wp/v2/media").mock(
        return_value=httpx.Response(201, json={"id": 7, "source_url": "http://wp.local/wp-content/uploads/x.png"})
    )

    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake-png-bytes")
    result = WordPressClient("http://wp.local", "editor", "pw").upload_media(image_path)

    assert result == {"id": 7, "source_url": "http://wp.local/wp-content/uploads/x.png"}
    request = route.calls[0].request
    assert request.headers["content-disposition"] == 'attachment; filename="cover.png"'
    assert request.headers["content-type"] == "image/png"
    assert request.content == b"fake-png-bytes"


@respx.mock
def test_list_categories_and_tags():
    respx.get("http://wp.local/wp-json/wp/v2/categories", params={"per_page": "100"}).mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "News"}])
    )
    respx.get("http://wp.local/wp-json/wp/v2/tags", params={"per_page": "100"}).mock(
        return_value=httpx.Response(200, json=[{"id": 5, "name": "launch"}])
    )

    client = WordPressClient("http://wp.local", "editor", "pw")

    assert client.list_categories() == [{"id": 1, "name": "News"}]
    assert client.list_tags() == [{"id": 5, "name": "launch"}]
