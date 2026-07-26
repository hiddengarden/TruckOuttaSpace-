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


class ComposeState(TypedDict):
    """Phase 1: draft -> supervise -> revise loop -> escalate. No ComfyUI or
    Postiz dependency at all, so a whole batch of these can run purely
    against Ollama before any GPU-contending ComfyUI work starts -- see
    run.py's two-phase batching, which exists specifically so Ollama and
    ComfyUI (sharing one GPU) are never both under load at once."""

    brand: dict
    topic: str
    draft: str
    verdicts: list[dict]
    approved: bool
    rejected_reason: Optional[str]


def initial_compose_state(brand: BrandContext, topic: str) -> ComposeState:
    return {
        "brand": asdict(brand),
        "topic": topic,
        "draft": "",
        "verdicts": [],
        "approved": False,
        "rejected_reason": None,
    }


def build_compose_graph(
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    knowledge_base: KnowledgeBase | None = None,
) -> StateGraph:
    """Durability/interrupt earn their keep specifically at "escalate": a
    Director escalation can sit paused for hours or days (the checkpointer
    persists state across processes), then get resumed later via
    Command(resume=...) -- a plain function call would need the process to
    stay alive the whole time instead.
    """

    def content_node(state: ComposeState) -> dict:
        brand = BrandContext(**state["brand"])
        draft = content_agent.draft(brand, state["topic"], knowledge_base)
        return {"draft": draft}

    def supervisor_node(state: ComposeState) -> dict:
        brand = BrandContext(**state["brand"])
        verdict = supervisor_agent.review(brand, state["draft"])
        update: dict = {"verdicts": state["verdicts"] + [verdict.model_dump()]}
        if verdict.approved:
            update["approved"] = True
        elif verdict.revised_text:
            update["draft"] = verdict.revised_text
        return update

    def route_after_supervisor(state: ComposeState) -> str:
        if state["approved"]:
            return "approved"
        last_verdict = state["verdicts"][-1]
        if last_verdict.get("revised_text") and len(state["verdicts"]) < MAX_SUPERVISOR_ROUNDS:
            return "supervisor"
        return "escalate"

    def escalate_node(state: ComposeState) -> dict:
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

    def route_after_escalate(state: ComposeState) -> str:
        return "approved" if state["approved"] else END

    builder = StateGraph(ComposeState)
    builder.add_node("content", content_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("escalate", escalate_node)

    builder.add_edge(START, "content")
    builder.add_edge("content", "supervisor")
    builder.add_conditional_edges(
        "supervisor", route_after_supervisor, {"supervisor": "supervisor", "escalate": "escalate", "approved": END}
    )
    builder.add_conditional_edges("escalate", route_after_escalate, {"approved": END, END: END})

    return builder


class FinalizeState(TypedDict):
    """Phase 2: illustrate (best-effort -- a ComfyUI failure must not block
    publishing already-approved text) -> publish. Reads `brand`/`draft` from
    whatever a compose-graph run already persisted to this thread's
    checkpoint (empirically verified: state survives across differently
    shaped compiled graphs sharing a thread_id + checkpointer)."""

    brand: dict
    draft: str
    integration_ids: list[str]
    postiz_group_id: Optional[str]
    post_type: str
    image_brief: Optional[str]
    image_media: Optional[dict]
    postiz_response: Optional[dict]
    publish_attempted: bool


def initial_finalize_input(
    integration_ids: list[str],
    postiz_group_id: str | None,
    post_type: PostType = "draft",
    image_brief: str | None = None,
) -> dict:
    # Deliberately does NOT set image_media/postiz_response/publish_attempted.
    # Those are only ever written by this graph's own nodes. Explicit input
    # values always override checkpoint values on invoke (verified
    # empirically) -- if a driver re-invokes this same helper for a thread
    # that already has checkpoint history (e.g. after a crash), setting
    # these to falsy defaults here would silently erase the idempotency
    # markers the finalize graph depends on to avoid a duplicate publish.
    return {
        "integration_ids": integration_ids,
        "postiz_group_id": postiz_group_id,
        "post_type": post_type,
        "image_brief": image_brief,
    }


def build_finalize_graph(
    postiz_client: PostizClient | None,
    artist: Artist | None = None,
    image_assets_dir: str | Path | None = None,
) -> StateGraph:
    def illustrate_node(state: FinalizeState) -> dict:
        if artist is None or postiz_client is None or not state.get("image_brief"):
            return {}
        try:
            brand = BrandContext(**state["brand"])
            paths = artist.generate(brand, state["image_brief"], image_assets_dir or ".")
            if not paths:
                return {}
            media = postiz_client.upload_media(paths[0])
            return {"image_media": {"id": media["id"], "path": media["path"]}}
        except Exception:
            # Best-effort: image generation failing must not block publishing
            # the already-approved text. Caller can inspect logs/ledger for why.
            return {}

    def route_before_illustrate(state: FinalizeState) -> str:
        # Idempotency guard, checked before illustrate runs at all (a plain
        # re-invoke after a crash restarts from START, so this must be the
        # first thing that happens or a retry wastes a ComfyUI generation
        # before ever reaching the check).
        #
        # If a prior run already marked this thread as "publish attempted"
        # but never got as far as recording a response, we can't tell
        # whether the actual Postiz call succeeded before the process died.
        # Retrying risks a duplicate draft; skipping is the safer failure --
        # it's visible (the ledger/logs show it) and recoverable by a human,
        # unlike a silent duplicate.
        if state.get("publish_attempted") and state.get("postiz_response") is None:
            return "ambiguous"
        if state.get("postiz_response") is not None:
            return "already_published"
        return "illustrate"

    def mark_attempt_node(state: FinalizeState) -> dict:
        return {"publish_attempted": True}

    def publish_node(state: FinalizeState) -> dict:
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

    def ambiguous_node(state: FinalizeState) -> dict:
        return {}

    def noop_node(state: FinalizeState) -> dict:
        return {}

    builder = StateGraph(FinalizeState)
    builder.add_node("illustrate", illustrate_node)
    builder.add_node("mark_attempt", mark_attempt_node)
    builder.add_node("publish", publish_node)
    builder.add_node("ambiguous", ambiguous_node)
    builder.add_node("already_published", noop_node)

    builder.add_conditional_edges(
        START,
        route_before_illustrate,
        {"illustrate": "illustrate", "ambiguous": "ambiguous", "already_published": "already_published"},
    )
    builder.add_edge("illustrate", "mark_attempt")
    builder.add_edge("mark_attempt", "publish")
    builder.add_edge("publish", END)
    builder.add_edge("ambiguous", END)
    builder.add_edge("already_published", END)

    return builder
