from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agency.agents.supervisor_agent import Verdict
from agency.graph import build_post_graph, initial_post_state
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
    def __init__(self):
        self.calls = []
        self.uploads = []

    def create_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "post_1"}

    def upload_media(self, file_path):
        self.uploads.append(file_path)
        return {"id": "media_1", "path": "/uploads/img.png"}


class FakeArtist:
    def __init__(self):
        self.calls = []

    def generate(self, brand, brief, assets_dir, **kwargs):
        self.calls.append(brief)
        out = Path(assets_dir) / "generated.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [out]


def _compile(supervisor_verdicts, postiz, artist=None, image_assets_dir=None):
    return build_post_graph(
        FakeContentAgent(), FakeSupervisorAgent(supervisor_verdicts), postiz, artist=artist, image_assets_dir=image_assets_dir
    ).compile(checkpointer=MemorySaver())


def test_publishes_on_first_approval():
    postiz = FakePostizClient()
    graph = _compile([Verdict(approved=True, reason="ok")], postiz)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")

    output = graph.invoke(state, {"configurable": {"thread_id": "t1"}})

    assert output["approved"] is True
    assert "__interrupt__" not in output
    assert postiz.calls[0]["content"] == "draft about launch"


def test_revises_once_then_publishes():
    postiz = FakePostizClient()
    verdicts = [
        Verdict(approved=False, reason="meh", revised_text="better draft"),
        Verdict(approved=True, reason="good now"),
    ]
    graph = _compile(verdicts, postiz)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")

    output = graph.invoke(state, {"configurable": {"thread_id": "t2"}})

    assert output["approved"] is True
    assert len(output["verdicts"]) == 2
    assert postiz.calls[0]["content"] == "better draft"


def test_escalates_when_no_revision_offered():
    postiz = FakePostizClient()
    graph = _compile([Verdict(approved=False, reason="off-brand")], postiz)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")

    output = graph.invoke(state, {"configurable": {"thread_id": "t3"}})

    assert "__interrupt__" in output
    assert postiz.calls == []


def test_escalates_after_exhausting_revision_rounds():
    postiz = FakePostizClient()
    verdicts = [
        Verdict(approved=False, reason="meh", revised_text="still not it"),
        Verdict(approved=False, reason="still meh", revised_text="another try"),
    ]
    graph = _compile(verdicts, postiz)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")

    output = graph.invoke(state, {"configurable": {"thread_id": "t4"}})

    assert "__interrupt__" in output
    assert len(output["verdicts"]) == 2
    assert postiz.calls == []


def test_escalation_resumed_with_approval_publishes_replacement_text():
    postiz = FakePostizClient()
    graph = _compile([Verdict(approved=False, reason="off-brand")], postiz)
    config = {"configurable": {"thread_id": "t5"}}
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")
    graph.invoke(state, config)

    output = graph.invoke(Command(resume={"approved": True, "text": "human-fixed draft"}), config)

    assert output["approved"] is True
    assert postiz.calls[0]["content"] == "human-fixed draft"


def test_escalation_resumed_with_rejection_never_publishes():
    postiz = FakePostizClient()
    graph = _compile([Verdict(approved=False, reason="off-brand")], postiz)
    config = {"configurable": {"thread_id": "t6"}}
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")
    graph.invoke(state, config)

    output = graph.invoke(Command(resume={"approved": False, "reason": "not on brand"}), config)

    assert output["approved"] is False
    assert output["rejected_reason"] == "not on brand"
    assert postiz.calls == []


def test_dry_run_with_no_postiz_client_skips_publish_call():
    graph = _compile([Verdict(approved=True, reason="ok")], None)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")

    output = graph.invoke(state, {"configurable": {"thread_id": "t7"}})

    assert output["approved"] is True
    assert output["postiz_response"] is None


def test_illustrate_attaches_uploaded_image_when_artist_and_brief_set(tmp_path):
    postiz = FakePostizClient()
    artist = FakeArtist()
    graph = _compile([Verdict(approved=True, reason="ok")], postiz, artist=artist, image_assets_dir=tmp_path)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1", image_brief="a launch photo")

    output = graph.invoke(state, {"configurable": {"thread_id": "t8"}})

    assert artist.calls == ["a launch photo"]
    assert postiz.uploads == [tmp_path / "generated.png"]
    assert output["image_media"] == {"id": "media_1", "path": "/uploads/img.png"}
    assert postiz.calls[0]["images"] == [{"id": "media_1", "path": "/uploads/img.png"}]


def test_illustrate_is_a_noop_without_image_brief(tmp_path):
    postiz = FakePostizClient()
    artist = FakeArtist()
    graph = _compile([Verdict(approved=True, reason="ok")], postiz, artist=artist, image_assets_dir=tmp_path)
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1")  # no image_brief

    output = graph.invoke(state, {"configurable": {"thread_id": "t9"}})

    assert artist.calls == []
    assert output["image_media"] is None
    assert postiz.calls[0]["images"] is None


def test_illustrate_is_a_noop_without_artist_even_with_brief():
    postiz = FakePostizClient()
    graph = _compile([Verdict(approved=True, reason="ok")], postiz)  # no artist configured
    state = initial_post_state(_BRAND, "launch", ["int_1"], "group_1", image_brief="a launch photo")

    output = graph.invoke(state, {"configurable": {"thread_id": "t10"}})

    assert postiz.uploads == []
    assert output["image_media"] is None
