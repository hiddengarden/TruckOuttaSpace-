from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

from agency.agents.artist import Artist
from agency.agents.content_agent import ContentAgent
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.escalations import EscalationRegistry
from agency.graph import build_post_graph, initial_post_state
from agency.knowledge import KnowledgeBase
from agency.org import Brand, Customer, Project, brand_context, effective_posts_per_run
from agency.postiz.client import PostizClient
from agency.state import TopicHistory


@dataclass
class PostOutcome:
    topic: str
    thread_id: str
    status: str  # "published" | "rejected" | "pending_review"
    postiz_response: dict | None = None


@dataclass
class RunResult:
    customer_slug: str
    brand_slug: str
    project_slug: str | None
    ingest_errors: list[str] = field(default_factory=list)
    outcomes: list[PostOutcome] = field(default_factory=list)


def run_brand_project(
    customer: Customer,
    brand: Brand,
    project: Project | None,
    knowledge_root: str,
    state_root: str,
    checkpointer,
    knowledge_agent: KnowledgeAgent,
    topic_agent: TopicAgent,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
    escalations: EscalationRegistry,
    artist: Artist | None = None,
    assets_root: str | None = None,
) -> RunResult:
    project_slug = project.slug if project else None
    result = RunResult(customer_slug=customer.slug, brand_slug=brand.slug, project_slug=project_slug)

    for url in brand.knowledge_sources:
        try:
            knowledge_agent.ingest_url(customer.slug, brand.slug, url)
        except (RobotsDisallowed, httpx.HTTPError) as exc:
            result.ingest_errors.append(f"{url}: {exc}")

    knowledge_base = KnowledgeBase(customer.slug, brand.slug, knowledge_root)
    topic_history = TopicHistory(state_root, customer.slug, brand.slug, project_slug)
    ctx = brand_context(brand, project)

    topics = topic_agent.propose(
        ctx, knowledge_base, topic_history.recent(), count=effective_posts_per_run(brand, project)
    )

    image_assets_dir = (
        Path(assets_root) / customer.slug / brand.slug / "generated" / "images" if artist is not None else None
    )
    graph = build_post_graph(
        content_agent, supervisor_agent, postiz_client, knowledge_base, artist=artist, image_assets_dir=image_assets_dir
    ).compile(checkpointer=checkpointer)

    for topic in topics:
        thread_id = f"{customer.slug}:{brand.slug}:{project_slug or '_default'}:{uuid4().hex[:8]}"
        image_brief = topic if artist is not None else None
        state = initial_post_state(ctx, topic, brand.integration_ids, brand.postiz_group_id, image_brief=image_brief)
        output = graph.invoke(state, {"configurable": {"thread_id": thread_id}})
        topic_history.record(topic)
        result.outcomes.append(_to_outcome(topic, thread_id, output, customer, brand, project_slug, escalations))

    return result


def run_all(
    customers: list[Customer],
    knowledge_root: str,
    state_root: str,
    checkpointer,
    knowledge_agent: KnowledgeAgent,
    topic_agent: TopicAgent,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
    escalations: EscalationRegistry,
    artist: Artist | None = None,
    assets_root: str | None = None,
) -> list[RunResult]:
    results = []
    for customer in customers:
        for brand in customer.brands:
            targets: list[Project | None] = list(brand.projects) if brand.projects else [None]
            for project in targets:
                results.append(
                    run_brand_project(
                        customer,
                        brand,
                        project,
                        knowledge_root,
                        state_root,
                        checkpointer,
                        knowledge_agent,
                        topic_agent,
                        content_agent,
                        supervisor_agent,
                        postiz_client,
                        escalations,
                        artist=artist,
                        assets_root=assets_root,
                    )
                )
    return results


def resume_escalation(
    checkpointer,
    escalations: EscalationRegistry,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
    thread_id: str,
    approved: bool,
    text: str | None = None,
    reason: str | None = None,
    artist: Artist | None = None,
    image_assets_dir: str | None = None,
) -> dict:
    from langgraph.types import Command

    graph = build_post_graph(
        content_agent, supervisor_agent, postiz_client, artist=artist, image_assets_dir=image_assets_dir
    ).compile(checkpointer=checkpointer)
    output = graph.invoke(
        Command(resume={"approved": approved, "text": text, "reason": reason}),
        {"configurable": {"thread_id": thread_id}},
    )
    escalations.remove(thread_id)
    return output


def _to_outcome(topic, thread_id, output, customer, brand, project_slug, escalations) -> PostOutcome:
    if "__interrupt__" in output:
        interrupt_value = output["__interrupt__"][0].value
        escalations.add(
            thread_id,
            customer=customer.slug,
            brand=brand.slug,
            project=project_slug,
            topic=topic,
            draft=output.get("draft", ""),
            reason=interrupt_value.get("reason", ""),
        )
        return PostOutcome(topic=topic, thread_id=thread_id, status="pending_review")
    if output.get("approved"):
        return PostOutcome(
            topic=topic, thread_id=thread_id, status="published", postiz_response=output.get("postiz_response")
        )
    return PostOutcome(topic=topic, thread_id=thread_id, status="rejected")
