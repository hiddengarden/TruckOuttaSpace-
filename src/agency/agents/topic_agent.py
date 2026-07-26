import json

from agency.inference.provider import LLMProvider
from agency.knowledge import KnowledgeBase
from agency.org import BrandContext

_SYSTEM_TEMPLATE = (
    "You are the content strategist for {name}.\n"
    "Voice: {voice}\n"
    "Audience: {audience}\n"
    "Guidelines:\n{guidelines}\n"
    "Never propose a topic touching: {banned}.\n"
    "{focus_line}"
    "{knowledge_line}"
    "{history_line}"
    "Reply with ONLY a JSON array of exactly {count} distinct, specific post topic "
    "strings, nothing else."
)


class TopicAgent:
    """Proposes what to post about, grounded in the brand's knowledge base and avoiding repeats."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def propose(
        self,
        brand: BrandContext,
        knowledge_base: KnowledgeBase,
        recent_topics: list[str],
        count: int = 1,
    ) -> list[str]:
        titles = knowledge_base.titles()
        system = _SYSTEM_TEMPLATE.format(
            name=brand.name,
            voice=brand.voice,
            audience=brand.audience,
            guidelines="\n".join(f"- {g}" for g in brand.guidelines),
            banned=", ".join(brand.banned_topics),
            focus_line=f"Current focus: {brand.topic_hint}.\n" if brand.topic_hint else "",
            knowledge_line=f"Brand knowledge base covers: {', '.join(titles)}.\n" if titles else "",
            history_line=(
                f"Already posted recently, do not repeat: {', '.join(recent_topics)}.\n"
                if recent_topics
                else ""
            ),
            count=count,
        )
        raw = self._provider.complete(system, "Propose the topics now.").strip()
        try:
            topics = json.loads(raw)
            if not isinstance(topics, list):
                raise ValueError("expected a JSON array")
        except (json.JSONDecodeError, ValueError):
            return [raw]
        return [str(topic) for topic in topics][:count]
