from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agency.escalations import EscalationRegistry
from agency.graph import build_compose_graph, initial_compose_state
from agency.agents.supervisor_agent import Verdict
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="plain", audience="everyone", guidelines=[], banned_topics=[])


class FakeContentAgent:
    def draft(self, brand, topic, knowledge_base=None):
        return f"draft about {topic}"


class FakeSupervisorAgent:
    def __init__(self, verdicts):
        self._verdicts = iter(verdicts)

    def review(self, brand, draft):
        return next(self._verdicts)


def _escalate_thread(checkpointer, thread_id: str) -> None:
    graph = build_compose_graph(FakeContentAgent(), FakeSupervisorAgent([Verdict(approved=False, reason="off-brand")])).compile(
        checkpointer=checkpointer
    )
    graph.invoke(initial_compose_state(_BRAND, "launch"), {"configurable": {"thread_id": thread_id}})


def test_list_verified_returns_entry_still_parked_at_escalate(tmp_path):
    checkpointer = MemorySaver()
    _escalate_thread(checkpointer, "t1")
    registry = EscalationRegistry(tmp_path)
    registry.add("t1", customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    verified = registry.list_verified(checkpointer)

    assert [e["thread_id"] for e in verified] == ["t1"]


def test_list_verified_prunes_entry_already_resolved_via_direct_resume(tmp_path):
    checkpointer = MemorySaver()
    _escalate_thread(checkpointer, "t2")
    registry = EscalationRegistry(tmp_path)
    registry.add("t2", customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    # Resolved directly against the graph, bypassing resume_escalation/registry.remove().
    graph = build_compose_graph(FakeContentAgent(), FakeSupervisorAgent([])).compile(checkpointer=checkpointer)
    graph.invoke(Command(resume={"approved": True, "text": "fixed"}), {"configurable": {"thread_id": "t2"}})

    verified = registry.list_verified(checkpointer)

    assert verified == []
    assert registry.get("t2") is None  # pruned from the JSON file too, not just filtered from this call


def test_list_verified_prunes_entry_with_no_matching_checkpoint(tmp_path):
    checkpointer = MemorySaver()
    registry = EscalationRegistry(tmp_path)
    registry.add("ghost", customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    verified = registry.list_verified(checkpointer)

    assert verified == []
    assert registry.get("ghost") is None


def test_list_verified_leaves_genuinely_pending_entries_alone_among_mixed_entries(tmp_path):
    checkpointer = MemorySaver()
    _escalate_thread(checkpointer, "pending-1")
    _escalate_thread(checkpointer, "resolved-1")
    registry = EscalationRegistry(tmp_path)
    registry.add("pending-1", customer="acme", brand="a", project=None, topic="t", draft="x", reason="r")
    registry.add("resolved-1", customer="acme", brand="b", project=None, topic="t", draft="x", reason="r")

    graph = build_compose_graph(FakeContentAgent(), FakeSupervisorAgent([])).compile(checkpointer=checkpointer)
    graph.invoke(Command(resume={"approved": True, "text": "fixed"}), {"configurable": {"thread_id": "resolved-1"}})

    verified = registry.list_verified(checkpointer)

    assert [e["thread_id"] for e in verified] == ["pending-1"]
    assert registry.get("resolved-1") is None
    assert registry.get("pending-1") is not None


def test_get_verified_returns_none_for_resolved_thread(tmp_path):
    checkpointer = MemorySaver()
    _escalate_thread(checkpointer, "t3")
    registry = EscalationRegistry(tmp_path)
    registry.add("t3", customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    graph = build_compose_graph(FakeContentAgent(), FakeSupervisorAgent([])).compile(checkpointer=checkpointer)
    graph.invoke(Command(resume={"approved": False, "reason": "no"}), {"configurable": {"thread_id": "t3"}})

    assert registry.get_verified("t3", checkpointer) is None


def test_get_verified_returns_entry_for_still_pending_thread(tmp_path):
    checkpointer = MemorySaver()
    _escalate_thread(checkpointer, "t4")
    registry = EscalationRegistry(tmp_path)
    registry.add("t4", customer="acme", brand="widgets", project=None, topic="launch", draft="x", reason="r")

    entry = registry.get_verified("t4", checkpointer)

    assert entry is not None
    assert entry["thread_id"] == "t4"
