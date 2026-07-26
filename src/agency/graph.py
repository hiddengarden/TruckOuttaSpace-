from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agency.agents.content_agent import ContentAgent
from agency.agents.supervisor_agent import SupervisorAgent
from agency.knowledge import KnowledgeBase
from agency.org import BrandContext
from agency.postiz.client import PostizClient, PostType

MAX_SUPERVISOR_ROUNDS = 2


class PostState(TypedDict):
    brand: dict
    topic: str
    draft: str
    verdicts: list[dict]
    approved: bool
    rejected_reason: Optional[str]
    postiz_response: Optional[dict]
    integration_ids: list[str]
    postiz_group_id: Optional[str]
    post_type: str


def initial_post_state(
    brand: BrandContext,
    topic: str,
    integration_ids: list[str],
    postiz_group_id: str | None,
    post_type: PostType = "draft",
) -> PostState:
    return {
        "brand": asdict(brand),
        "topic": topic,
        "draft": "",
        "verdicts": [],
        "approved": False,
        "rejected_reason": None,
        "postiz_response": None,
        "integration_ids": integration_ids,
        "postiz_group_id": postiz_group_id,
        "post_type": post_type,
    }


def build_post_graph(
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
    knowledge_base: KnowledgeBase | None = None,
) -> StateGraph:
    """One post's lifecycle: draft -> supervise -> (revise loop | escalate) -> publish.

    Durability/interrupt earn their keep specifically at "escalate": a Director
    escalation can sit paused for hours or days (the checkpointer persists
    state across processes), then get resumed later with Command(resume=...)
    -- a plain function call would need the process to stay alive the whole
    time instead.
    """

    def content_node(state: PostState) -> dict:
        brand = BrandContext(**state["brand"])
        draft = content_agent.draft(brand, state["topic"], knowledge_base)
        return {"draft": draft}

    def supervisor_node(state: PostState) -> dict:
        brand = BrandContext(**state["brand"])
        verdict = supervisor_agent.review(brand, state["draft"])
        update: dict = {"verdicts": state["verdicts"] + [verdict.model_dump()]}
        if verdict.approved:
            update["approved"] = True
        elif verdict.revised_text:
            update["draft"] = verdict.revised_text
        return update

    def route_after_supervisor(state: PostState) -> str:
        if state["approved"]:
            return "publish"
        last_verdict = state["verdicts"][-1]
        if last_verdict.get("revised_text") and len(state["verdicts"]) < MAX_SUPERVISOR_ROUNDS:
            return "supervisor"
        return "escalate"

    def escalate_node(state: PostState) -> dict:
        decision = interrupt(
            {
                "reason": "Supervisor could not approve after revisions; escalating to a human.",
                "draft": state["draft"],
                "verdicts": state["verdicts"],
            }
        )
        if decision.get("approved"):
            return {"approved": True, "draft": decision.get("text", state["draft"])}
        return {"approved": False, "rejected_reason": decision.get("reason", "rejected by human")}

    def route_after_escalate(state: PostState) -> str:
        return "publish" if state["approved"] else END

    def publish_node(state: PostState) -> dict:
        if postiz_client is None:
            return {}
        response = postiz_client.create_post(
            post_type=state["post_type"],
            date_iso=datetime.now(timezone.utc).isoformat(),
            integration_ids=state["integration_ids"],
            content=state["draft"],
            group=state["postiz_group_id"],
        )
        return {"postiz_response": response}

    builder = StateGraph(PostState)
    builder.add_node("content", content_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("escalate", escalate_node)
    builder.add_node("publish", publish_node)

    builder.add_edge(START, "content")
    builder.add_edge("content", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"supervisor": "supervisor", "escalate": "escalate", "publish": "publish"},
    )
    builder.add_conditional_edges("escalate", route_after_escalate, {"publish": "publish", END: END})
    builder.add_edge("publish", END)

    return builder
