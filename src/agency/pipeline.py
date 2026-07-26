from dataclasses import dataclass, field
from datetime import datetime, timezone

from agency.agents.content_agent import ContentAgent
from agency.agents.supervisor_agent import SupervisorAgent, Verdict
from agency.brand import BrandProfile
from agency.postiz.client import PostizClient, PostType

MAX_REVISION_ROUNDS = 2


@dataclass
class PipelineResult:
    final_text: str
    verdicts: list[Verdict] = field(default_factory=list)
    approved: bool = False
    postiz_response: dict | None = None


def run_pipeline(
    brand: BrandProfile,
    topic: str,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None = None,
    post_type: PostType = "draft",
) -> PipelineResult:
    text = content_agent.draft(brand, topic)
    verdicts: list[Verdict] = []

    for _ in range(MAX_REVISION_ROUNDS):
        verdict = supervisor_agent.review(brand, text)
        verdicts.append(verdict)
        if verdict.approved:
            break
        if verdict.revised_text:
            text = verdict.revised_text
            continue
        break

    result = PipelineResult(final_text=text, verdicts=verdicts, approved=verdicts[-1].approved)

    if result.approved and postiz_client is not None:
        result.postiz_response = postiz_client.create_post(
            post_type=post_type,
            date_iso=datetime.now(timezone.utc).isoformat(),
            integration_ids=brand.integration_ids,
            content=text,
            group=brand.postiz_group_id,
        )

    return result
