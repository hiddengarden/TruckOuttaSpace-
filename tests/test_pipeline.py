from agency.agents.supervisor_agent import Verdict
from agency.brand import load_brand
from agency.pipeline import run_pipeline


class FakeContentAgent:
    def draft(self, brand, topic):
        return f"draft about {topic}"


class FakeSupervisorAgent:
    def __init__(self, verdicts):
        self._verdicts = iter(verdicts)

    def review(self, brand, draft):
        return next(self._verdicts)


class FakePostizClient:
    def __init__(self):
        self.calls = []

    def create_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "post_1"}


def test_pipeline_publishes_on_first_approval():
    brand = load_brand("brands/example_brand.yaml")
    postiz = FakePostizClient()

    result = run_pipeline(
        brand=brand,
        topic="new product launch",
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent([Verdict(approved=True, reason="looks good")]),
        postiz_client=postiz,
    )

    assert result.approved
    assert result.final_text == "draft about new product launch"
    assert len(postiz.calls) == 1
    assert postiz.calls[0]["content"] == "draft about new product launch"


def test_pipeline_uses_revision_then_approves():
    brand = load_brand("brands/example_brand.yaml")
    postiz = FakePostizClient()

    result = run_pipeline(
        brand=brand,
        topic="sale",
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(
            [
                Verdict(approved=False, reason="too salesy", revised_text="revised copy"),
                Verdict(approved=True, reason="better"),
            ]
        ),
        postiz_client=postiz,
    )

    assert result.approved
    assert result.final_text == "revised copy"
    assert len(result.verdicts) == 2
    assert postiz.calls[0]["content"] == "revised copy"


def test_pipeline_does_not_publish_when_never_approved():
    brand = load_brand("brands/example_brand.yaml")
    postiz = FakePostizClient()

    result = run_pipeline(
        brand=brand,
        topic="risky topic",
        content_agent=FakeContentAgent(),
        supervisor_agent=FakeSupervisorAgent(
            [
                Verdict(approved=False, reason="bad", revised_text="still bad"),
                Verdict(approved=False, reason="still bad"),
            ]
        ),
        postiz_client=postiz,
    )

    assert not result.approved
    assert postiz.calls == []
