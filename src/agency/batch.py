from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agency.agents.content_agent import ContentAgent
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.brand import BrandProfile, load_brand
from agency.knowledge import KnowledgeBase
from agency.pipeline import PipelineResult, run_pipeline
from agency.postiz.client import PostizClient
from agency.state import TopicHistory


@dataclass
class BrandRunResult:
    brand_slug: str
    ingest_errors: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    pipeline_results: list[PipelineResult] = field(default_factory=list)


def run_brand(
    brand: BrandProfile,
    knowledge_root: str,
    state_root: str,
    knowledge_agent: KnowledgeAgent,
    topic_agent: TopicAgent,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
) -> BrandRunResult:
    result = BrandRunResult(brand_slug=brand.slug)

    for url in brand.knowledge_sources:
        try:
            knowledge_agent.ingest_url(brand, url)
        except (RobotsDisallowed, httpx.HTTPError) as exc:
            result.ingest_errors.append(f"{url}: {exc}")

    knowledge_base = KnowledgeBase(brand, knowledge_root)
    topic_history = TopicHistory(state_root, brand)

    result.topics = topic_agent.propose(
        brand, knowledge_base, topic_history.recent(), count=brand.posts_per_run
    )

    for topic in result.topics:
        pipeline_result = run_pipeline(
            brand=brand,
            topic=topic,
            content_agent=content_agent,
            supervisor_agent=supervisor_agent,
            postiz_client=postiz_client,
            knowledge_base=knowledge_base,
        )
        result.pipeline_results.append(pipeline_result)
        topic_history.record(topic)

    return result


def run_all(
    brand_paths: list[Path],
    knowledge_root: str,
    state_root: str,
    knowledge_agent: KnowledgeAgent,
    topic_agent: TopicAgent,
    content_agent: ContentAgent,
    supervisor_agent: SupervisorAgent,
    postiz_client: PostizClient | None,
) -> list[BrandRunResult]:
    results = []
    for path in brand_paths:
        brand = load_brand(path)
        results.append(
            run_brand(
                brand,
                knowledge_root,
                state_root,
                knowledge_agent,
                topic_agent,
                content_agent,
                supervisor_agent,
                postiz_client,
            )
        )
    return results
