import httpx
import respx

from agency.paperless.client import PaperlessClient


@respx.mock
def test_list_documents_sends_token_header_and_filters():
    route = respx.get("http://paperless.local/api/documents/", params={"tags__id": "3"}).mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": 1, "title": "Invoice", "content": "some OCR text"}], "next": None}
        )
    )

    client = PaperlessClient("http://paperless.local", "secret-token")
    docs = client.list_documents(tag_id=3)

    assert docs == [{"id": 1, "title": "Invoice", "content": "some OCR text"}]
    request = route.calls[0].request
    assert request.headers["authorization"] == "Token secret-token"


@respx.mock
def test_list_documents_follows_pagination():
    # respx's params= matching is a subset match, not exact -- the page-2
    # request also carries tags__id=3, so it would match a params={"tags__id":
    # "3"} route too. Match the exact full URL for each page instead so the
    # two are unambiguous.
    respx.get("http://paperless.local/api/documents/?tags__id=3").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": 1, "title": "Doc 1"}],
                "next": "http://paperless.local/api/documents/?tags__id=3&page=2",
            },
        )
    )
    respx.get("http://paperless.local/api/documents/?tags__id=3&page=2").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 2, "title": "Doc 2"}], "next": None})
    )

    docs = PaperlessClient("http://paperless.local", "secret-token").list_documents(tag_id=3)

    assert [d["id"] for d in docs] == [1, 2]


@respx.mock
def test_list_documents_with_no_filters_sends_no_filter_params():
    route = respx.get("http://paperless.local/api/documents/").mock(
        return_value=httpx.Response(200, json={"results": [], "next": None})
    )

    PaperlessClient("http://paperless.local", "secret-token").list_documents()

    assert route.calls[0].request.url.params == httpx.QueryParams()


@respx.mock
def test_list_tags_correspondents_document_types():
    respx.get("http://paperless.local/api/tags/", params={"page_size": "100"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": 1, "name": "brand:widgets"}]})
    )
    respx.get("http://paperless.local/api/correspondents/", params={"page_size": "100"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": 2, "name": "Acme Supplier"}]})
    )
    respx.get("http://paperless.local/api/document_types/", params={"page_size": "100"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": 3, "name": "Invoice"}]})
    )

    client = PaperlessClient("http://paperless.local", "secret-token")

    assert client.list_tags() == [{"id": 1, "name": "brand:widgets"}]
    assert client.list_correspondents() == [{"id": 2, "name": "Acme Supplier"}]
    assert client.list_document_types() == [{"id": 3, "name": "Invoice"}]


@respx.mock
def test_find_tag_id_matches_case_insensitively():
    respx.get("http://paperless.local/api/tags/", params={"page_size": "100"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": 1, "name": "Brand:Widgets"}]})
    )

    client = PaperlessClient("http://paperless.local", "secret-token")

    assert client.find_tag_id("brand:widgets") == 1
    assert client.find_tag_id("nonexistent") is None
