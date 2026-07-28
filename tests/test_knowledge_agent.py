import json

import httpx
import pytest
import respx

from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed

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


@respx.mock
def test_ingest_url_writes_markdown_and_downloads_images(tmp_path):
    respx.get("https://example-co.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example-co.test/about").mock(return_value=httpx.Response(200, text=PAGE_HTML))
    respx.get("https://example-co.test/images/hero.jpg").mock(
        return_value=httpx.Response(200, content=b"fake-jpeg-bytes", headers={"content-type": "image/jpeg"})
    )

    agent = KnowledgeAgent(tmp_path)
    doc = agent.ingest_url("example-customer", "example-brand", "https://example-co.test/about")

    assert doc.title == "Our Story"
    assert doc.asset_paths == ["example-customer/example-brand/assets/our-story/img-0.jpg"]

    markdown_path = tmp_path / doc.markdown_path
    assert markdown_path.exists()
    content = markdown_path.read_text()
    assert "source_url: https://example-co.test/about" in content
    assert "widgets for small businesses" in content

    image_path = tmp_path / doc.asset_paths[0]
    assert image_path.read_bytes() == b"fake-jpeg-bytes"

    manifest = json.loads((tmp_path / "example-customer" / "example-brand" / "manifest.json").read_text())
    assert len(manifest) == 1
    assert manifest[0]["id"] == "our-story"


# --- ingest_paperless ---


class FakePaperlessClient:
    def __init__(self, documents):
        self._documents = documents
        self.calls = []

    def list_documents(self, **filters):
        self.calls.append(filters)
        return self._documents


def test_ingest_paperless_writes_ocr_content_into_the_corpus(tmp_path):
    paperless = FakePaperlessClient([{"id": 42, "title": "Widgets Invoice #1", "content": "Total: $500"}])
    agent = KnowledgeAgent(tmp_path / "knowledge")

    docs = agent.ingest_paperless("acme", "widgets", paperless, tag_id=3)

    assert len(docs) == 1
    assert docs[0].title == "Widgets Invoice #1"
    assert paperless.calls == [{"tag_id": 3, "correspondent_id": None, "document_type_id": None, "query": None}]

    content = (tmp_path / "knowledge" / docs[0].markdown_path).read_text()
    assert "source_url: paperless:document:42" in content
    assert "Total: $500" in content

    manifest = json.loads((tmp_path / "knowledge" / "acme" / "widgets" / "manifest.json").read_text())
    assert manifest[0]["id"] == docs[0].id


def test_ingest_paperless_falls_back_to_id_when_title_missing(tmp_path):
    paperless = FakePaperlessClient([{"id": 7, "title": "", "content": "text"}])
    agent = KnowledgeAgent(tmp_path / "knowledge")

    docs = agent.ingest_paperless("acme", "widgets", paperless)

    assert docs[0].title == "document-7"


def test_reingesting_same_paperless_document_replaces_manifest_entry(tmp_path):
    agent = KnowledgeAgent(tmp_path / "knowledge")
    agent.ingest_paperless("acme", "widgets", FakePaperlessClient([{"id": 1, "title": "Doc", "content": "v1"}]))
    agent.ingest_paperless("acme", "widgets", FakePaperlessClient([{"id": 1, "title": "Doc", "content": "v2"}]))

    manifest = json.loads((tmp_path / "knowledge" / "acme" / "widgets" / "manifest.json").read_text())
    assert len(manifest) == 1
    content = (tmp_path / "knowledge" / manifest[0]["markdown_path"]).read_text()
    assert "v2" in content


@respx.mock
def test_ingest_url_respects_robots_disallow(tmp_path):
    respx.get("https://example-co.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )

    agent = KnowledgeAgent(tmp_path)
    with pytest.raises(RobotsDisallowed):
        agent.ingest_url("example-customer", "example-brand", "https://example-co.test/about")

    assert not (tmp_path / "example-customer").exists()


@respx.mock
def test_reingesting_same_page_replaces_manifest_entry(tmp_path):
    respx.get("https://example-co.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example-co.test/about").mock(return_value=httpx.Response(200, text=PAGE_HTML))
    respx.get("https://example-co.test/images/hero.jpg").mock(
        return_value=httpx.Response(200, content=b"v1", headers={"content-type": "image/jpeg"})
    )

    agent = KnowledgeAgent(tmp_path)
    agent.ingest_url("example-customer", "example-brand", "https://example-co.test/about")
    agent.ingest_url("example-customer", "example-brand", "https://example-co.test/about")

    manifest = json.loads((tmp_path / "example-customer" / "example-brand" / "manifest.json").read_text())
    assert len(manifest) == 1


# --- ingest_local_folder (Obsidian vault / any folder of markdown) ---


def test_ingest_local_folder_reads_every_md_file_recursively(tmp_path):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "brand-voice.md").write_text("# Brand Voice\n\nWe are confident and friendly.")
    (vault / "top-level.md").write_text("no heading here, just text")

    agent = KnowledgeAgent(tmp_path / "knowledge")
    docs = agent.ingest_local_folder("acme", "widgets", vault)

    assert {d.title for d in docs} == {"Brand Voice", "top-level"}
    manifest = json.loads((tmp_path / "knowledge" / "acme" / "widgets" / "manifest.json").read_text())
    assert len(manifest) == 2

    brand_voice = next(d for d in docs if d.title == "Brand Voice")
    content = (tmp_path / "knowledge" / brand_voice.markdown_path).read_text()
    assert f"source_url: {vault / 'notes' / 'brand-voice.md'}" in content
    assert "confident and friendly" in content


def test_ingest_local_folder_raises_clearly_on_missing_folder(tmp_path):
    agent = KnowledgeAgent(tmp_path / "knowledge")

    with pytest.raises(NotADirectoryError):
        agent.ingest_local_folder("acme", "widgets", tmp_path / "does-not-exist")


def test_ingest_local_folder_skips_obsidian_and_syncthing_internal_dirs(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".obsidian" / "workspace.md").write_text("not real content")
    (vault / ".stversions" / ".obsidian").mkdir(parents=True)
    (vault / ".stversions" / "old-note.md").write_text("stale syncthing backup")
    (vault / "real-note.md").write_text("# Real Note\n\nactual content")

    agent = KnowledgeAgent(tmp_path / "knowledge")
    docs = agent.ingest_local_folder("acme", "widgets", vault)

    assert [d.title for d in docs] == ["Real Note"]


def test_ingest_local_folder_copies_referenced_local_images(tmp_path):
    vault = tmp_path / "vault"
    (vault / "attachments").mkdir(parents=True)
    (vault / "attachments" / "logo.png").write_bytes(b"fake-png-bytes")
    (vault / "note.md").write_text("# Note\n\n![our logo](attachments/logo.png)\n\nsome text")

    agent = KnowledgeAgent(tmp_path / "knowledge")
    docs = agent.ingest_local_folder("acme", "widgets", vault)

    assert len(docs[0].asset_paths) == 1
    image_path = tmp_path / "knowledge" / docs[0].asset_paths[0]
    assert image_path.read_bytes() == b"fake-png-bytes"


def test_ingest_local_folder_skips_missing_or_remote_image_references(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "note.md").write_text(
        "# Note\n\n![missing](attachments/does-not-exist.png)\n![remote](https://example.test/x.png)"
    )

    agent = KnowledgeAgent(tmp_path / "knowledge")
    docs = agent.ingest_local_folder("acme", "widgets", vault)

    assert docs[0].asset_paths == []


def test_reingesting_same_local_folder_replaces_manifest_entries(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "note.md").write_text("# Note\n\nv1")

    agent = KnowledgeAgent(tmp_path / "knowledge")
    agent.ingest_local_folder("acme", "widgets", vault)
    (vault / "note.md").write_text("# Note\n\nv2")
    agent.ingest_local_folder("acme", "widgets", vault)

    manifest = json.loads((tmp_path / "knowledge" / "acme" / "widgets" / "manifest.json").read_text())
    assert len(manifest) == 1
    content = (tmp_path / "knowledge" / manifest[0]["markdown_path"]).read_text()
    assert "v2" in content
