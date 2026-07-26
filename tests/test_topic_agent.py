import json

from agency.agents.topic_agent import TopicAgent
from agency.knowledge import KnowledgeBase
from agency.org import BrandContext

_BRAND = BrandContext(
    name="Example Co",
    voice="Confident and friendly",
    audience="Small business owners",
    guidelines=["Include a call to action"],
    banned_topics=["politics"],
)


class RecordingProvider:
    def __init__(self, reply: str):
        self.reply = reply
        self.last_system = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


def test_propose_parses_json_array_and_respects_count():
    kb = KnowledgeBase("example-customer", "example-brand", "does-not-exist")
    provider = RecordingProvider(json.dumps(["topic one", "topic two", "topic three"]))

    topics = TopicAgent(provider).propose(_BRAND, kb, recent_topics=[], count=2)

    assert topics == ["topic one", "topic two"]


def test_propose_includes_recent_topics_focus_and_knowledge_titles_in_prompt(tmp_path):
    pages_dir = tmp_path / "example-customer" / "example-brand" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "a.md").write_text("content")
    manifest_path = tmp_path / "example-customer" / "example-brand" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "source_url": "https://example.test/a",
                    "title": "Flagship Widget",
                    "markdown_path": "example-customer/example-brand/pages/a.md",
                    "asset_paths": [],
                    "fetched_at": "2026-07-26T00:00:00+00:00",
                }
            ]
        )
    )
    kb = KnowledgeBase("example-customer", "example-brand", tmp_path)
    provider = RecordingProvider(json.dumps(["new topic"]))
    focused_brand = BrandContext(**{**_BRAND.__dict__, "topic_hint": "the fall launch"})

    TopicAgent(provider).propose(focused_brand, kb, recent_topics=["old topic"], count=1)

    assert "Flagship Widget" in provider.last_system
    assert "old topic" in provider.last_system
    assert "the fall launch" in provider.last_system


def test_propose_falls_back_to_raw_text_on_unparseable_reply():
    kb = KnowledgeBase("example-customer", "example-brand", "does-not-exist")
    provider = RecordingProvider("not json")

    topics = TopicAgent(provider).propose(_BRAND, kb, recent_topics=[], count=1)

    assert topics == ["not json"]
