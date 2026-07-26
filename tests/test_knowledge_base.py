import json

from agency.brand import load_brand
from agency.knowledge import KnowledgeBase


def _write_doc(root, brand_slug, doc_id, title, content, source_url="https://example.test/x"):
    pages_dir = root / brand_slug / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = pages_dir / f"{doc_id}.md"
    markdown_path.write_text(content)

    manifest_path = root / brand_slug / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest.append(
        {
            "id": doc_id,
            "source_url": source_url,
            "title": title,
            "markdown_path": str(markdown_path.relative_to(root)),
            "asset_paths": [],
            "fetched_at": "2026-07-26T00:00:00+00:00",
        }
    )
    manifest_path.write_text(json.dumps(manifest))


def test_retrieve_ranks_by_keyword_overlap(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    _write_doc(tmp_path, brand.slug, "widgets", "Our Widgets", "We sell durable widgets for offices.")
    _write_doc(
        tmp_path, brand.slug, "unrelated", "Company Holidays", "The office is closed in July for the season."
    )

    kb = KnowledgeBase(brand, tmp_path)
    results = kb.retrieve("durable widgets for small offices", k=2)

    assert [doc.title for doc in results][0] == "Our Widgets"
    assert results[0].score > results[1].score


def test_retrieve_returns_empty_when_no_corpus(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    kb = KnowledgeBase(brand, tmp_path)
    assert kb.retrieve("anything") == []
    assert kb.context_block("anything") == ""


def test_context_block_includes_source_attribution(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    _write_doc(
        tmp_path, brand.slug, "widgets", "Our Widgets", "We sell durable widgets.",
        source_url="https://example.test/widgets",
    )

    kb = KnowledgeBase(brand, tmp_path)
    block = kb.context_block("widgets")

    assert "Our Widgets" in block
    assert "https://example.test/widgets" in block
    assert "durable widgets" in block
