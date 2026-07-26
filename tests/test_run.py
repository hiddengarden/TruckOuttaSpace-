import json
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver

from agency.agents.knowledge_agent import RobotsDisallowed
from agency.escalations import EscalationRegistry
from agency.graph import build_finalize_graph
from agency.inference.provider import LocalInferenceUnavailable
from agency.ledger import RunLedger
from agency.org import Brand, Customer, Project
from agency.run import resume_escalation, run_all, run_finalize

CALL_ORDER: list[str] = []


class ScriptedProvider:
    """Distinguishes which agent is calling by matching this repo's real
    system-prompt wording, so run_all's orchestration can be tested without
    needing separate fakes for TopicAgent/ContentAgent/SupervisorAgent --
    run_all only ever gets a provider_factory, not pre-built agents."""

    def __init__(self, approve: bool = True, track_order: bool = False):
        self._approve = approve
        self._track_order = track_order

    def complete(self, system, user):
        if "content strategist" in system:
            if self._track_order:
                CALL_ORDER.append("topic")
            return json.dumps(["topic-0"])
        if "brand-safety supervisor" in system:
            if self._track_order:
                CALL_ORDER.append("supervisor")
            return json.dumps({"approved": self._approve, "reason": "ok" if self._approve else "no"})
        if self._track_order:
            CALL_ORDER.append("content")
        return "draft text"

    def complete_with_image(self, *a, **k):
        raise NotImplementedError


class RaisingProvider:
    def complete(self, system, user):
        raise LocalInferenceUnavailable("local model unreachable, fallback not allowed")

    def complete_with_image(self, *a, **k):
        raise LocalInferenceUnavailable("local model unreachable, fallback not allowed")


class FakeKnowledgeAgent:
    def __init__(self):
        self.ingested = []

    def ingest_url(self, customer_slug, brand_slug, url):
        if "blocked" in url:
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self.ingested.append((customer_slug, brand_slug, url))


class FakePostizClient:
    def __init__(self):
        self.calls = []
        self.uploads = []

    def create_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "post_1"}

    def upload_media(self, file_path):
        self.uploads.append(file_path)
        return {"id": "media_1", "path": "/uploads/img.png"}


class TrackingArtist:
    def __init__(self, provider=None, comfyui_client=None, workflows_dir=None):
        pass

    def generate(self, brand, brief, assets_dir, **kwargs):
        CALL_ORDER.append("illustrate")
        out = Path(assets_dir) / "img.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [out]


def _brand(slug="widgets", knowledge_sources=None, projects=None, active=True, allow_cloud_fallback=False):
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
        active=active,
        allow_cloud_fallback=allow_cloud_fallback,
    )


def _run_all(customers, tmp_path, provider_factory=None, comfyui_client=None, only=None):
    provider_factory = provider_factory or (lambda allow_fallback: ScriptedProvider(approve=True))
    return run_all(
        customers,
        knowledge_root=str(tmp_path / "knowledge"),
        state_root=str(tmp_path / "state"),
        checkpointer=MemorySaver(),
        knowledge_agent=FakeKnowledgeAgent(),
        provider_factory=provider_factory,
        postiz_client=FakePostizClient(),
        escalations=EscalationRegistry(tmp_path / "state"),
        ledger=RunLedger(tmp_path / "state"),
        ollama_base_url="http://localhost:11434/v1",
        ollama_model="llama3.1",
        comfyui_client=comfyui_client,
        workflows_dir="workflows/image",
        assets_root=str(tmp_path / "assets"),
        only=only,
    )


def test_run_all_publishes_an_approved_topic(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand()])
    results = _run_all([customer], tmp_path)

    assert len(results) == 1
    assert results[0].outcomes[0].status == "published"
    assert results[0].outcomes[0].postiz_response == {"id": "post_1"}


def test_run_all_records_ingest_errors_but_still_composes(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand(knowledge_sources=["https://x.test/blocked"])])
    results = _run_all([customer], tmp_path)

    assert len(results[0].ingest_errors) == 1
    assert len(results[0].outcomes) == 1


def test_run_all_skips_inactive_brands(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand(slug="active-brand"), _brand(slug="paused-brand", active=False)])
    results = _run_all([customer], tmp_path)

    assert [r.brand_slug for r in results] == ["active-brand"]


def test_run_all_skips_inactive_projects(tmp_path):
    active_project = Project(slug="p1", name="P1", active=True)
    paused_project = Project(slug="p2", name="P2", active=False)
    customer = Customer(slug="acme", name="Acme", brands=[_brand(projects=[active_project, paused_project])])
    results = _run_all([customer], tmp_path)

    assert [r.project_slug for r in results] == ["p1"]


def test_run_all_only_filter_targets_specific_brand(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand(slug="brand-a"), _brand(slug="brand-b")])
    results = _run_all([customer], tmp_path, only={("acme", "brand-b")})

    assert [r.brand_slug for r in results] == ["brand-b"]


def test_run_all_unapproved_topic_escalates_instead_of_silently_rejecting(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand()])
    results = _run_all([customer], tmp_path, provider_factory=lambda allow_fallback: ScriptedProvider(approve=False))

    # Compose always escalates instead of rejecting outright on a fresh
    # thread (escalate_node interrupts unconditionally on first entry) --
    # a human resolves it via `agency resume`, never a silent auto-reject.
    assert results[0].outcomes[0].status == "pending_review"


def test_run_all_local_inference_unavailable_is_recorded_and_skipped(tmp_path):
    customer = Customer(slug="acme", name="Acme", brands=[_brand()])
    results = _run_all([customer], tmp_path, provider_factory=lambda allow_fallback: RaisingProvider())

    ledger_entries = RunLedger(tmp_path / "state").read_all()
    assert any(e["event"] == "local_inference_unavailable" for e in ledger_entries)
    assert results[0].outcomes == []  # never even got to propose a topic


@patch("agency.run.Artist", TrackingArtist)
@patch("agency.run.unload_ollama")
def test_run_all_composes_globally_before_finalizing_and_unloads_ollama_between(mock_unload, tmp_path):
    CALL_ORDER.clear()
    customer_a = Customer(slug="acme", name="Acme", brands=[_brand(slug="brand-a")])
    customer_b = Customer(slug="beta", name="Beta", brands=[_brand(slug="brand-b")])

    _run_all(
        [customer_a, customer_b],
        tmp_path,
        provider_factory=lambda allow_fallback: ScriptedProvider(approve=True, track_order=True),
        comfyui_client=object(),  # any non-None sentinel -- run_all only checks "is not None"
    )

    last_compose_index = max(i for i, c in enumerate(CALL_ORDER) if c != "illustrate")
    first_illustrate_index = min(i for i, c in enumerate(CALL_ORDER) if c == "illustrate")
    assert last_compose_index < first_illustrate_index, CALL_ORDER
    mock_unload.assert_called_once_with("http://localhost:11434/v1", "llama3.1")


def test_run_all_does_not_unload_ollama_when_no_comfyui_client_configured(tmp_path):
    with patch("agency.run.unload_ollama") as mock_unload:
        customer = Customer(slug="acme", name="Acme", brands=[_brand()])
        _run_all([customer], tmp_path, comfyui_client=None)
        mock_unload.assert_not_called()


# --- run_finalize idempotency guard (driver-level check before invoking) ---


def _seed_compose_output(graph, thread_id: str, draft: str = "approved draft") -> None:
    # run_finalize is always called, in production, against a thread a
    # compose-graph run already persisted brand/draft into. Seed that
    # directly here rather than running a full compose graph first.
    graph.update_state({"configurable": {"thread_id": thread_id}}, {"brand": {}, "draft": draft})


def test_run_finalize_publishes_on_first_call(tmp_path):
    postiz = FakePostizClient()
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())
    _seed_compose_output(graph, "f1")
    ledger = RunLedger(tmp_path / "state")

    output = run_finalize(graph, "f1", ["int_1"], "group_1", "draft", None, ledger, "acme", "widgets")

    assert output["postiz_response"] == {"id": "post_1"}
    assert any(e["event"] == "finalize_done" for e in ledger.read_all())


def test_run_finalize_skips_already_published_without_recalling_create_post(tmp_path):
    postiz = FakePostizClient()
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())
    _seed_compose_output(graph, "f2")
    ledger = RunLedger(tmp_path / "state")

    run_finalize(graph, "f2", ["int_1"], "group_1", "draft", None, ledger, "acme", "widgets")
    run_finalize(graph, "f2", ["int_1"], "group_1", "draft", None, ledger, "acme", "widgets")

    assert len(postiz.calls) == 1


def test_run_finalize_skips_ambiguous_state_without_invoking(tmp_path):
    class CrashingPostizClient(FakePostizClient):
        def create_post(self, **kwargs):
            raise RuntimeError("simulated crash")

    postiz = CrashingPostizClient()
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())
    ledger = RunLedger(tmp_path / "state")
    config = {"configurable": {"thread_id": "f3"}}

    try:
        graph.invoke({"integration_ids": ["int_1"], "postiz_group_id": "group_1", "post_type": "draft", "brand": {}, "draft": "x"}, config)
    except RuntimeError:
        pass

    output = run_finalize(graph, "f3", ["int_1"], "group_1", "draft", None, ledger, "acme", "widgets")

    assert output == {}
    assert postiz.calls == []
    assert any(e["event"] == "finalize_ambiguous_skip" for e in ledger.read_all())


# --- resume_escalation ---


class FakeContentAgent:
    def draft(self, brand, topic, knowledge_base=None):
        return f"draft:{topic}"


class NeverApprovingSupervisor:
    def review(self, brand, draft):
        from agency.agents.supervisor_agent import Verdict

        return Verdict(approved=False, reason="never good enough")


def test_resume_escalation_removes_from_registry_on_approval(tmp_path):
    from agency.graph import build_compose_graph, initial_compose_state
    from agency.org import BrandContext

    checkpointer = MemorySaver()
    compose_graph = build_compose_graph(FakeContentAgent(), NeverApprovingSupervisor()).compile(checkpointer=checkpointer)
    ctx = BrandContext(name="Example", voice="v", audience="a", guidelines=[], banned_topics=[])
    thread_id = "escalated-thread"
    compose_graph.invoke(initial_compose_state(ctx, "launch"), {"configurable": {"thread_id": thread_id}})

    escalations = EscalationRegistry(tmp_path / "state")
    escalations.add(thread_id, customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    output = resume_escalation(
        checkpointer, escalations, FakeContentAgent(), NeverApprovingSupervisor(), thread_id, approved=True, text="human text"
    )

    assert output["approved"] is True
    assert output["draft"] == "human text"
    assert escalations.get(thread_id) is None
