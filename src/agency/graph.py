from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agency.agents.artist import Artist
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
    image_brief: Optional[str]
    image_media: Optional[dict]


def initial_post_state(
    brand: BrandContext,
    topic: str,
    integration_ids: list[str],
    postiz_group_id: str | None,
    post_type: PostType = "draft",
    image_brief: str | None = None,
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
        "image_brief": image_brief,
        "image_media": None,
    }


def build_post_graph(
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
    knowledge_base: KnowledgeBase | None = None,
    artist: Artist | None = None,
    image_assets_dir: str | Path | None = None,
) -> StateGraph:
    """One post's lifecycle: draft -> supervise -> (revise loop | escalate) ->
    illustrate -> publish.

    Durability/interrupt earn their keep specifically at "escalate": a Director
    escalation can sit paused for hours or days (the checkpointer persists
    state across processes), then get resumed later with Command(resume=...)
    -- a plain function call would need the process to stay alive the whole
    time instead.

    "illustrate" is a no-op unless both `artist` and the run's `image_brief`
    are set: it generates one image via Artist, uploads it through
    PostizClient.upload_media(), and attaches the resulting media ref to the
    post before publish.
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

    def illustrate_node(state: PostState) -> dict:
        if artist is None or postiz_client is None or not state.get("image_brief"):
            return {}
        brand = BrandContext(**state["brand"])
        paths = artist.generate(brand, state["image_brief"], image_assets_dir or ".")
        if not paths:
            return {}
        media = postiz_client.upload_media(paths[0])
        return {"image_media": {"id": media["id"], "path": media["path"]}}

    def publish_node(state: PostState) -> dict:
        if postiz_client is None:
            return {}
        image_media = state.get("image_media")
        response = postiz_client.create_post(
            post_type=state["post_type"],
            date_iso=datetime.now(timezone.utc).isoformat(),
            integration_ids=state["integration_ids"],
            content=state["draft"],
            group=state["postiz_group_id"],
            images=[image_media] if image_media else None,
        )
        return {"postiz_response": response}

    builder = StateGraph(PostState)
    builder.add_node("content", content_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("escalate", escalate_node)
    builder.add_node("illustrate", illustrate_node)
    builder.add_node("publish", publish_node)

    builder.add_edge(START, "content")
    builder.add_edge("content", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"supervisor": "supervisor", "escalate": "escalate", "publish": "illustrate"},
    )
    builder.add_conditional_edges("escalate", route_after_escalate, {"publish": "illustrate", END: END})
    builder.add_edge("illustrate", "publish")
    builder.add_edge("publish", END)

    return builder
