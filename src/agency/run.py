from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import httpx

from agency.agents.artist import Artist
from agency.agents.content_agent import ContentAgent
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.comfyui.client import ComfyUIClient
from agency.escalations import EscalationRegistry
from agency.graph import build_compose_graph, build_finalize_graph, initial_compose_state, initial_finalize_input
from agency.inference.provider import LLMProvider, LocalInferenceUnavailable, unload_ollama
from agency.knowledge import KnowledgeBase
from agency.ledger import RunLedger
from agency.org import Brand, Customer, Project, brand_context, effective_posts_per_run
from agency.paperless.client import PaperlessClient
from agency.postiz.client import PostizClient
from agency.state import TopicHistory


@dataclass
class PostOutcome:
    topic: str
    thread_id: str
    status: str  # "published" | "rejected" | "pending_review" | "failed"
    postiz_response: dict | None = None


@dataclass
class RunResult:
    customer_slug: str
    brand_slug: str
    project_slug: str | None
    ingest_errors: list[str] = field(default_factory=list)
    outcomes: list[PostOutcome] = field(default_factory=list)


@dataclass
class _PendingFinalization:
    key: tuple[str, str, str | None]
    topic: str
    thread_id: str
    brand: Brand
    image_brief: str | None


def run_all(
    customers: list[Customer],
    knowledge_root: str,
    state_root: str,
    checkpointer,
    knowledge_agent: KnowledgeAgent,
    provider_factory: Callable[[bool], LLMProvider],
    postiz_client: PostizClient | None,
    escalations: EscalationRegistry,
    ledger: RunLedger,
    ollama_base_url: str,
    ollama_model: str,
    comfyui_client: ComfyUIClient | None = None,
    workflows_dir: str | None = None,
    assets_root: str | None = None,
    only: set[tuple[str, str]] | None = None,
    paperless_client: PaperlessClient | None = None,
) -> list[RunResult]:
    """Two global phases, not per-brand: every active customer/brand/project's
    text work (Ollama) runs to completion before any ComfyUI-heavy image work
    starts, across the WHOLE run -- not just within one brand. GPU
    contention between Ollama and ComfyUI is a system-wide resource, so
    interleaving them per-brand would still contend across brand boundaries;
    only a genuinely global split avoids it.

    `only`, if given, is a set of (customer_slug, brand_slug) pairs -- any
    brand not in it is skipped, same as if it were inactive, letting an
    operator target a specific brand for a one-off run without touching
    org config.
    """
    results: dict[tuple[str, str, str | None], RunResult] = {}
    pending: list[_PendingFinalization] = []

    for customer in customers:
        for brand in customer.brands:
            if not brand.active:
                continue
            if only is not None and (customer.slug, brand.slug) not in only:
                continue

            targets: list[Project | None] = [p for p in brand.projects if p.active] if brand.projects else [None]
            for project in targets:
                key = (customer.slug, brand.slug, project.slug if project else None)
                results[key] = RunResult(customer_slug=customer.slug, brand_slug=brand.slug, project_slug=key[2])

                for url in brand.knowledge_sources:
                    try:
                        knowledge_agent.ingest_url(customer.slug, brand.slug, url)
                    except (RobotsDisallowed, httpx.HTTPError) as exc:
                        results[key].ingest_errors.append(f"{url}: {exc}")

                for folder in brand.knowledge_folders:
                    try:
                        knowledge_agent.ingest_local_folder(customer.slug, brand.slug, folder)
                    except OSError as exc:
                        results[key].ingest_errors.append(f"{folder}: {exc}")

                if brand.knowledge_paperless_tag and paperless_client is not None:
                    try:
                        tag_id = paperless_client.find_tag_id(brand.knowledge_paperless_tag)
                        if tag_id is None:
                            results[key].ingest_errors.append(
                                f"paperless tag '{brand.knowledge_paperless_tag}' not found"
                            )
                        else:
                            knowledge_agent.ingest_paperless(customer.slug, brand.slug, paperless_client, tag_id=tag_id)
                    except httpx.HTTPError as exc:
                        results[key].ingest_errors.append(f"paperless: {exc}")

                knowledge_base = KnowledgeBase(customer.slug, brand.slug, knowledge_root)
                topic_history = TopicHistory(state_root, customer.slug, brand.slug, key[2])
                ctx = brand_context(brand, project)
                provider = provider_factory(brand.allow_cloud_fallback)
                topic_agent = TopicAgent(provider)

                try:
                    topics = topic_agent.propose(
                        ctx, knowledge_base, topic_history.recent(), count=effective_posts_per_run(brand, project)
                    )
                except LocalInferenceUnavailable as exc:
                    ledger.record(
                        "local_inference_unavailable", customer=customer.slug, brand=brand.slug, project=key[2],
                        stage="propose_topics", error=str(exc),
                    )
                    continue

                compose_graph = build_compose_graph(
                    ContentAgent(provider), SupervisorAgent(provider), knowledge_base
                ).compile(checkpointer=checkpointer)

                for topic in topics:
                    thread_id = f"{customer.slug}:{brand.slug}:{key[2] or '_default'}:{uuid4().hex[:8]}"
                    try:
                        output = compose_graph.invoke(
                            initial_compose_state(ctx, topic), {"configurable": {"thread_id": thread_id}}
                        )
                    except LocalInferenceUnavailable as exc:
                        ledger.record(
                            "local_inference_unavailable", thread_id=thread_id, customer=customer.slug,
                            brand=brand.slug, project=key[2], topic=topic, error=str(exc),
                        )
                        results[key].outcomes.append(PostOutcome(topic=topic, thread_id=thread_id, status="failed"))
                        continue

                    topic_history.record(topic)
                    ledger.record(
                        "compose_done", thread_id=thread_id, customer=customer.slug, brand=brand.slug,
                        project=key[2], topic=topic, approved=bool(output.get("approved")),
                        escalated="__interrupt__" in output, revisions=len(output.get("verdicts", [])),
                    )

                    if "__interrupt__" in output:
                        # A fresh compose thread can only reach here or the
                        # "approved" branch below -- escalate_node always
                        # interrupts on first entry, so "not approved, no
                        # interrupt" is unreachable on a first invoke (only
                        # possible after a resume, which run_all never does
                        # inline).
                        interrupt_value = output["__interrupt__"][0].value
                        escalations.add(
                            thread_id, customer=customer.slug, brand=brand.slug, project=key[2], topic=topic,
                            draft=output.get("draft", ""), reason=interrupt_value.get("reason", ""),
                        )
                        results[key].outcomes.append(
                            PostOutcome(topic=topic, thread_id=thread_id, status="pending_review")
                        )
                    else:
                        image_brief = topic if comfyui_client is not None else None
                        pending.append(_PendingFinalization(key, topic, thread_id, brand, image_brief))

    if comfyui_client is not None and pending:
        unload_ollama(ollama_base_url, ollama_model)

    for unit in pending:
        image_assets_dir = (
            Path(assets_root) / unit.key[0] / unit.key[1] / "generated" / "images" if assets_root else None
        )
        # Constructed per-unit, not shared across the whole run: Artist's own
        # prompt-writing call must respect THIS brand's allow_cloud_fallback,
        # not whichever brand happened to be processed first.
        artist = (
            Artist(provider_factory(unit.brand.allow_cloud_fallback), comfyui_client, workflows_dir)
            if comfyui_client is not None and unit.image_brief
            else None
        )
        finalize_graph = build_finalize_graph(postiz_client, artist, image_assets_dir).compile(
            checkpointer=checkpointer
        )
        output = run_finalize(
            finalize_graph, unit.thread_id, unit.brand.integration_ids, unit.brand.postiz_group_id, "draft",
            unit.image_brief, ledger, unit.key[0], unit.key[1],
        )
        results[unit.key].outcomes.append(
            PostOutcome(
                topic=unit.topic, thread_id=unit.thread_id,
                status="published" if output.get("postiz_response") else "failed",
                postiz_response=output.get("postiz_response"),
            )
        )

    return list(results.values())


def run_finalize(
    finalize_graph,
    thread_id: str,
    integration_ids: list[str],
    postiz_group_id: str | None,
    post_type: str,
    image_brief: str | None,
    ledger: RunLedger,
    customer_slug: str,
    brand_slug: str,
) -> dict:
    """Checks the checkpoint BEFORE invoking, so a retry never re-passes
    fresh input into a thread that already has an in-flight/ambiguous
    finalize attempt -- the primary defense; the graph's own routing (see
    graph.py) is defense-in-depth for direct callers of the graph.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = finalize_graph.get_state(config)

    if snapshot.values.get("publish_attempted") and snapshot.values.get("postiz_response") is None:
        ledger.record(
            "finalize_ambiguous_skip", thread_id=thread_id, customer=customer_slug, brand=brand_slug,
            reason="prior attempt marked but no recorded response -- possible crash mid-publish",
        )
        return {}
    if snapshot.values.get("postiz_response") is not None:
        return snapshot.values

    finalize_input = initial_finalize_input(integration_ids, postiz_group_id, post_type, image_brief)
    output = finalize_graph.invoke(finalize_input, config)
    ledger.record(
        "finalize_done", thread_id=thread_id, customer=customer_slug, brand=brand_slug,
        published=output.get("postiz_response") is not None,
    )
    return output


def resume_escalation(
    checkpointer,
    escalations: EscalationRegistry,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    thread_id: str,
    approved: bool,
    text: str | None = None,
    reason: str | None = None,
) -> dict:
    """Resumes the COMPOSE graph only -- escalation/interrupt only ever
    happens in compose now. If approved, the caller (cli.py) is responsible
    for separately invoking finalize (it knows the brand's integration_ids/
    postiz_group_id/image preferences; the escalation entry only stores
    enough to look the brand back up)."""
    from langgraph.types import Command

    graph = build_compose_graph(content_agent, supervisor_agent).compile(checkpointer=checkpointer)
    output = graph.invoke(
        Command(resume={"approved": approved, "text": text, "reason": reason}),
        {"configurable": {"thread_id": thread_id}},
    )
    escalations.remove(thread_id)
    return output
