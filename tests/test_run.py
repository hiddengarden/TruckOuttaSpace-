from langgraph.checkpoint.memory import MemorySaver

from agency.agents.knowledge_agent import RobotsDisallowed
from agency.agents.supervisor_agent import Verdict
from agency.escalations import EscalationRegistry
from agency.org import Brand, Customer, Project
from agency.run import resume_escalation, run_all, run_brand_project
from agency.state import TopicHistory


class FakeKnowledgeAgent:
    def __init__(self):
        self.ingested = []

    def ingest_url(self, customer_slug, brand_slug, url):
        if "blocked" in url:
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self.ingested.append((customer_slug, brand_slug, url))


class FakeTopicAgent:
    def propose(self, brand, knowledge_base, recent_topics, count=1):
        return [f"topic-{i}" for i in range(count)]


class FakeContentAgent:
    def draft(self, brand, topic, knowledge_base=None):
        return f"draft:{topic}"


class FakeSupervisorAgent:
    def review(self, brand, draft):
        return Verdict(approved=True, reason="ok")


class NeverApprovingSupervisor:
    def review(self, brand, draft):
        return Verdict(approved=False, reason="never good enough")


class FakePostizClient:
    def __init__(self):
        self.calls = []

    def create_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "post_1"}


def _brand(slug="widgets", knowledge_sources=None, projects=None):
    return Brand(
        slug=slug,
        name=slug.title(),
        postiz_group_id="g1",
        integration_ids=["int_1"],
        voice="plain",
        audience="everyone",
        knowledge_sources=knowledge_sources or [],
        posts_per_run=1,
        projects=projects or [],
    )


def test_run_brand_project_publishes_and_records_topic_history(tmp_path):
    customer = Customer(slug="acme", name="Acme")
    brand = _brand(knowledge_sources=["https://x.test/about"])
    postiz = FakePostizClient()
    escalations = EscalationRegistry(tmp_path / "state")

    result = run_brand_project(
        customer,
        brand,
        None,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        checkpointer=MemorySaver(),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=postiz,
        escalations=escalations,
    )

    assert result.ingest_errors == []
    assert len(result.outcomes) == 1
    assert result.outcomes[0].status == "published"
    assert postiz.calls[0]["content"] == "draft:topic-0"
    assert TopicHistory(tmp_path / "state", "acme", "widgets", None).recent() == ["topic-0"]


def test_run_brand_project_records_ingest_errors_but_still_drafts(tmp_path):
    customer = Customer(slug="acme", name="Acme")
    brand = _brand(knowledge_sources=["https://x.test/blocked"])
    escalations = EscalationRegistry(tmp_path / "state")

    result = run_brand_project(
        customer,
        brand,
        None,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        checkpointer=MemorySaver(),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=FakePostizClient(),
        escalations=escalations,
    )

    assert len(result.ingest_errors) == 1
    assert "blocked" in result.ingest_errors[0]
    assert len(result.outcomes) == 1


def test_run_all_fans_out_across_brands_and_projects(tmp_path):
    project = Project(slug="launch", name="Launch")
    customer = Customer(
        slug="acme", name="Acme", brands=[_brand(slug="widgets", projects=[project]), _brand(slug="gadgets")]
    )
    escalations = EscalationRegistry(tmp_path / "state")

    results = run_all(
        [customer],
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        checkpointer=MemorySaver(),
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(),
        postiz_client=FakePostizClient(),
        escalations=escalations,
    )

    assert [(r.brand_slug, r.project_slug) for r in results] == [("widgets", "launch"), ("gadgets", None)]


def test_escalation_is_registered_then_resumable(tmp_path):
    customer = Customer(slug="acme", name="Acme")
    brand = _brand()
    postiz = FakePostizClient()
    escalations = EscalationRegistry(tmp_path / "state")
    checkpointer = MemorySaver()

    result = run_brand_project(
        customer,
        brand,
        None,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        checkpointer=checkpointer,
        knowledge_agent=FakeKnowledgeAgent(),
        topic_agent=FakeTopicAgent(),
        content_agent=FakeContentAgent(),
        supervisor_agent=NeverApprovingSupervisor(),
        postiz_client=postiz,
        escalations=escalations,
    )

    assert result.outcomes[0].status == "pending_review"
    thread_id = result.outcomes[0].thread_id
    assert escalations.get(thread_id) is not None
    assert postiz.calls == []

    output = resume_escalation(
        checkpointer,
        escalations,
        content_agent=FakeContentAgent(),
        supervisor_agent=NeverApprovingSupervisor(),
        postiz_client=postiz,
        thread_id=thread_id,
        approved=True,
        text="human-approved text",
    )

    assert output["approved"] is True
    assert postiz.calls[0]["content"] == "human-approved text"
    assert escalations.get(thread_id) is None
