from dataclasses import asdict
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agency.agents.supervisor_agent import Verdict
from agency.graph import build_compose_graph, build_finalize_graph, initial_compose_state, initial_finalize_input
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


class FakePostizClient:
    def __init__(self, fail_first_n_publishes: int = 0):
        self.calls = []
        self.uploads = []
        self._fail_remaining = fail_first_n_publishes

    def create_post(self, **kwargs):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("simulated crash mid-publish")
        self.calls.append(kwargs)
        return {"id": "post_1"}

    def upload_media(self, file_path):
        self.uploads.append(file_path)
        return {"id": "media_1", "path": "/uploads/img.png"}


class FakeArtist:
    def __init__(self, fail: bool = False):
        self.calls = []
        self._fail = fail

    def generate(self, brand, brief, assets_dir, **kwargs):
        self.calls.append(brief)
        if self._fail:
            raise RuntimeError("ComfyUI unreachable")
        out = Path(assets_dir) / "generated.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [out]


def _compose(verdicts):
    return build_compose_graph(FakeContentAgent(), FakeSupervisorAgent(verdicts)).compile(checkpointer=MemorySaver())


# --- compose graph ---


def test_compose_approves_on_first_pass():
    graph = _compose([Verdict(approved=True, reason="ok")])
    output = graph.invoke(initial_compose_state(_BRAND, "launch"), {"configurable": {"thread_id": "c1"}})

    assert output["approved"] is True
    assert "__interrupt__" not in output
    assert output["draft"] == "draft about launch"


def test_compose_revises_once_then_approves():
    verdicts = [
        Verdict(approved=False, reason="meh", revised_text="better draft"),
        Verdict(approved=True, reason="good now"),
    ]
    graph = _compose(verdicts)
    output = graph.invoke(initial_compose_state(_BRAND, "launch"), {"configurable": {"thread_id": "c2"}})

    assert output["approved"] is True
    assert output["draft"] == "better draft"
    assert len(output["verdicts"]) == 2


def test_compose_escalates_when_no_revision_offered():
    graph = _compose([Verdict(approved=False, reason="off-brand")])
    output = graph.invoke(initial_compose_state(_BRAND, "launch"), {"configurable": {"thread_id": "c3"}})

    assert "__interrupt__" in output


def test_compose_escalates_after_exhausting_revision_rounds():
    verdicts = [
        Verdict(approved=False, reason="meh", revised_text="still not it"),
        Verdict(approved=False, reason="still meh", revised_text="another try"),
    ]
    graph = _compose(verdicts)
    output = graph.invoke(initial_compose_state(_BRAND, "launch"), {"configurable": {"thread_id": "c4"}})

    assert "__interrupt__" in output
    assert len(output["verdicts"]) == 2


def test_compose_escalation_resumed_with_approval():
    graph = _compose([Verdict(approved=False, reason="off-brand")])
    config = {"configurable": {"thread_id": "c5"}}
    graph.invoke(initial_compose_state(_BRAND, "launch"), config)

    output = graph.invoke(Command(resume={"approved": True, "text": "human-fixed draft"}), config)

    assert output["approved"] is True
    assert output["draft"] == "human-fixed draft"


def test_compose_escalation_resumed_with_rejection():
    graph = _compose([Verdict(approved=False, reason="off-brand")])
    config = {"configurable": {"thread_id": "c6"}}
    graph.invoke(initial_compose_state(_BRAND, "launch"), config)

    output = graph.invoke(Command(resume={"approved": False, "reason": "not on brand"}), config)

    assert output["approved"] is False
    assert output["rejected_reason"] == "not on brand"


# --- finalize graph ---


def _finalize_state(draft="approved draft", image_brief=None):
    return {**initial_finalize_input(["int_1"], "group_1", image_brief=image_brief), "brand": asdict(_BRAND), "draft": draft}


def test_finalize_publishes_without_image_when_no_brief():
    postiz = FakePostizClient()
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())

    output = graph.invoke(_finalize_state(), {"configurable": {"thread_id": "f1"}})

    assert output["postiz_response"] == {"id": "post_1"}
    assert postiz.calls[0]["content"] == "approved draft"
    assert postiz.calls[0]["images"] is None


def test_finalize_attaches_image_when_artist_and_brief_set(tmp_path):
    postiz = FakePostizClient()
    artist = FakeArtist()
    graph = build_finalize_graph(postiz, artist=artist, image_assets_dir=tmp_path).compile(checkpointer=MemorySaver())

    output = graph.invoke(_finalize_state(image_brief="a launch photo"), {"configurable": {"thread_id": "f2"}})

    assert artist.calls == ["a launch photo"]
    assert postiz.uploads == [tmp_path / "generated.png"]
    assert output["image_media"] == {"id": "media_1", "path": "/uploads/img.png"}
    assert postiz.calls[0]["images"] == [{"id": "media_1", "path": "/uploads/img.png"}]


def test_finalize_illustrate_failure_does_not_block_publish(tmp_path):
    postiz = FakePostizClient()
    artist = FakeArtist(fail=True)
    graph = build_finalize_graph(postiz, artist=artist, image_assets_dir=tmp_path).compile(checkpointer=MemorySaver())

    output = graph.invoke(_finalize_state(image_brief="a launch photo"), {"configurable": {"thread_id": "f3"}})

    assert output["postiz_response"] == {"id": "post_1"}
    assert postiz.calls[0]["images"] is None


def test_finalize_dry_run_with_no_postiz_client_skips_publish():
    graph = build_finalize_graph(None).compile(checkpointer=MemorySaver())

    output = graph.invoke(_finalize_state(), {"configurable": {"thread_id": "f4"}})

    assert output.get("postiz_response") is None


def test_finalize_does_not_republish_when_already_published():
    postiz = FakePostizClient()
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "f5"}}
    graph.invoke(_finalize_state(), config)
    assert len(postiz.calls) == 1

    # Re-invoking the same already-completed thread must not publish again.
    graph.invoke(_finalize_state(), config)
    assert len(postiz.calls) == 1


def test_finalize_idempotency_guard_skips_ambiguous_republish():
    # First attempt "crashes" inside publish_node after mark_attempt already
    # checkpointed -- this is the exact race the review flagged: a process
    # dying between the Postiz call and the next checkpoint write.
    postiz = FakePostizClient(fail_first_n_publishes=1)
    graph = build_finalize_graph(postiz).compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "f6"}}

    with pytest.raises(RuntimeError):
        graph.invoke(_finalize_state(), config)

    snapshot = graph.get_state(config)
    assert snapshot.values["publish_attempted"] is True
    assert snapshot.values.get("postiz_response") is None

    # A naive retry must NOT call create_post again -- ambiguous state,
    # fail visibly instead of risking a duplicate draft.
    output = graph.invoke(_finalize_state(), config)

    assert output.get("postiz_response") is None
    assert postiz.calls == []  # never actually succeeded, and never retried either
