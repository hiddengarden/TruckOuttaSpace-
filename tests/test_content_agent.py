import json

from agency.agents.content_agent import ContentAgent
from agency.brand import load_brand
from agency.knowledge import KnowledgeBase


class RecordingProvider:
    def __init__(self, reply: str = "generated post"):
        self.reply = reply
        self.last_system = None
        self.last_user = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self.reply


def test_draft_without_knowledge_base_has_no_knowledge_section():
    brand = load_brand("brands/example_brand.yaml")
    provider = RecordingProvider()

    ContentAgent(provider).draft(brand, "our new product")

    assert "Brand knowledge base" not in provider.last_system


def test_draft_includes_relevant_knowledge_context(tmp_path):
    brand = load_brand("brands/example_brand.yaml")

    pages_dir = tmp_path / brand.slug / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "widgets.md").write_text("Our flagship widget ships in 24 hours.")
    manifest_path = tmp_path / brand.slug / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "id": "widgets",
                    "source_url": "https://example.test/widgets",
                    "title": "Flagship Widget",
                    "markdown_path": f"{brand.slug}/pages/widgets.md",
                    "asset_paths": [],
                    "fetched_at": "2026-07-26T00:00:00+00:00",
                }
            ]
        )
    )

    provider = RecordingProvider()
    kb = KnowledgeBase(brand, tmp_path)

    ContentAgent(provider).draft(brand, "our flagship widget", knowledge_base=kb)

    assert "Brand knowledge base" in provider.last_system
    assert "ships in 24 hours" in provider.last_system
    assert "https://example.test/widgets" in provider.last_system
