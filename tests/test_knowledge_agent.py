import json

import httpx
import pytest
import respx

from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.brand import load_brand

PAGE_HTML = """
<html>
  <head><title>Our Story</title></head>
  <body>
    <article>
      <h1>Our Story</h1>
      <p>We make widgets for small businesses.</p>
      <img src="/images/hero.jpg" />
    </article>
  </body>
</html>
"""


@pytest.fixture
def brand():
    return load_brand("brands/example_brand.yaml")


@respx.mock
def test_ingest_url_writes_markdown_and_downloads_images(tmp_path, brand):
    respx.get("https://example-co.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example-co.test/about").mock(return_value=httpx.Response(200, text=PAGE_HTML))
    respx.get("https://example-co.test/images/hero.jpg").mock(
        return_value=httpx.Response(200, content=b"fake-jpeg-bytes", headers={"content-type": "image/jpeg"})
    )

    agent = KnowledgeAgent(tmp_path)
    doc = agent.ingest_url(brand, "https://example-co.test/about")

    assert doc.title == "Our Story"
    assert doc.asset_paths == ["example-brand/assets/our-story/img-0.jpg"]

    markdown_path = tmp_path / doc.markdown_path
    assert markdown_path.exists()
    content = markdown_path.read_text()
    assert "source_url: https://example-co.test/about" in content
    assert "widgets for small businesses" in content

    image_path = tmp_path / doc.asset_paths[0]
    assert image_path.read_bytes() == b"fake-jpeg-bytes"

    manifest = json.loads((tmp_path / "example-brand" / "manifest.json").read_text())
    assert len(manifest) == 1
    assert manifest[0]["id"] == "our-story"


@respx.mock
def test_ingest_url_respects_robots_disallow(tmp_path, brand):
    respx.get("https://example-co.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )

    agent = KnowledgeAgent(tmp_path)
    with pytest.raises(RobotsDisallowed):
        agent.ingest_url(brand, "https://example-co.test/about")

    assert not (tmp_path / "example-brand").exists()


@respx.mock
def test_reingesting_same_page_replaces_manifest_entry(tmp_path, brand):
    respx.get("https://example-co.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example-co.test/about").mock(return_value=httpx.Response(200, text=PAGE_HTML))
    respx.get("https://example-co.test/images/hero.jpg").mock(
        return_value=httpx.Response(200, content=b"v1", headers={"content-type": "image/jpeg"})
    )

    agent = KnowledgeAgent(tmp_path)
    agent.ingest_url(brand, "https://example-co.test/about")
    agent.ingest_url(brand, "https://example-co.test/about")

    manifest = json.loads((tmp_path / "example-brand" / "manifest.json").read_text())
    assert len(manifest) == 1
