from agency.agents.knowledge_agent import RobotsDisallowed
from agency.agents.supervisor_agent import Verdict
from agency.batch import run_all, run_brand
from agency.brand import load_brand
from agency.state import TopicHistory


class FakeKnowledgeAgent:
    def __init__(self):
        self.ingested = []

    def ingest_url(self, brand, url):
        if "blocked" in url:
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self.ingested.append(url)


class FakeTopicAgent:
    def propose(self, brand, knowledge_base, recent_topics, count=1):
        return [f"topic-{i}" for i in range(count)]


class FakeContentAgent:
    def draft(self, brand, topic, knowledge_base=None):
        return f"draft:{topic}"


class FakeSupervisorAgent:
    def review(self, brand, draft):
        return Verdict(approved=True, reason="ok")


class FakePostizClient:
    def __init__(self):
        self.calls = []

    def create_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "post_1"}


def _write_brand(tmp_path, slug, knowledge_sources):
    path = tmp_path / f"{slug}.yaml"
    sources = "\n".join(f'  - "{url}"' for url in knowledge_sources) or "  []"
    path.write_text(
        f"slug: {slug}\n"
        f"name: {slug.title()}\n"
        "postiz_group_id: g1\n"
        "integration_ids: [int_1]\n"
        "voice: plain\n"
        "audience: everyone\n"
        "knowledge_sources:\n"
        f"{sources}\n"
        "posts_per_run: 1\n"
    )
    return path


def test_run_brand_ingests_publishes_and_records_topic_history(tmp_path):
    brand = load_brand("brands/example_brand.yaml")  # has one non-"blocked" knowledge source
    postiz = FakePostizClient()

    result = run_brand(
        brand,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=postiz,
    )

    assert result.ingest_errors == []
    assert result.topics == ["topic-0"]
    assert len(result.pipeline_results) == 1
    assert result.pipeline_results[0].approved
    assert postiz.calls[0]["content"] == "draft:topic-0"
    assert TopicHistory(tmp_path / "state", brand).recent() == ["topic-0"]


def test_run_brand_records_robots_disallowed_as_ingest_error(tmp_path):
    brand_path = _write_brand(tmp_path, "blocked-brand", ["https://x.test/blocked"])
    brand = load_brand(brand_path)

    result = run_brand(
        brand,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=FakePostizClient(),
    )

    assert len(result.ingest_errors) == 1
    assert "blocked" in result.ingest_errors[0]
    assert len(result.pipeline_results) == 1  # ingest failure doesn't stop drafting


def test_run_all_processes_every_brand_file(tmp_path):
    _write_brand(tmp_path, "brand-a", [])
    _write_brand(tmp_path, "brand-b", [])
    brand_paths = sorted(tmp_path.glob("*.yaml"))

    results = run_all(
        brand_paths,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=FakePostizClient(),
    )

    assert [r.brand_slug for r in results] == ["brand-a", "brand-b"]
