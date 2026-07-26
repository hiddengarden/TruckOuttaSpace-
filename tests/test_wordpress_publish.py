from pathlib import Path

from agency.agents.ghost_writer import Chapter, Publication
from agency.wordpress.publish import publish_publication_to_wordpress


class FakeWordPressClient:
    def __init__(self):
        self.uploaded = []
        self.created_post = None

    def upload_media(self, file_path):
        self.uploaded.append(Path(file_path))
        return {"id": len(self.uploaded), "source_url": f"http://wp.local/uploads/{Path(file_path).name}"}

    def create_post(self, **kwargs):
        self.created_post = kwargs
        return {"id": 1, "link": "http://wp.local/?p=1"}


def test_publish_uploads_cover_as_featured_media():
    client = FakeWordPressClient()
    publication = Publication(
        title="A Guide", cover_image_path=Path("/assets/cover.png"), chapters=[Chapter(title="Intro", content="## Intro\ntext")]
    )

    publish_publication_to_wordpress(client, publication, status="publish")

    assert client.uploaded == [Path("/assets/cover.png")]
    assert client.created_post["featured_media"] == 1
    assert client.created_post["status"] == "publish"
    assert client.created_post["title"] == "A Guide"


def test_publish_rewrites_chapter_illustration_links_to_uploaded_urls():
    client = FakeWordPressClient()
    publication = Publication(
        title="A Guide",
        chapters=[
            Chapter(title="Intro", content="## Intro\ntext", illustration_path=Path("/assets/intro.png")),
        ],
    )

    publish_publication_to_wordpress(client, publication)

    html = client.created_post["content"]
    assert "/assets/intro.png" not in html
    assert "http://wp.local/uploads/intro.png" in html


def test_publish_converts_markdown_headings_to_html():
    client = FakeWordPressClient()
    publication = Publication(title="A Guide", chapters=[Chapter(title="Intro", content="## Intro\nSome text.")])

    publish_publication_to_wordpress(client, publication)

    html = client.created_post["content"]
    assert "<h2>Intro</h2>" in html
    assert "<p>Some text.</p>" in html


def test_publish_without_cover_or_illustrations_passes_no_featured_media():
    # The real WordPressClient.create_post omits featured_media from the
    # request body when it's None (see test_wordpress_client.py) -- here we
    # only need to confirm publish_publication_to_wordpress passes None
    # through rather than fabricating an id.
    client = FakeWordPressClient()
    publication = Publication(title="A Guide", chapters=[Chapter(title="Intro", content="text")])

    publish_publication_to_wordpress(client, publication)

    assert client.uploaded == []
    assert client.created_post["featured_media"] is None
