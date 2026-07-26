from agency.brand import BrandProfile
from agency.inference.provider import LLMProvider
from agency.knowledge import KnowledgeBase


class ContentAgent:
    """Drafts social post copy in a brand's voice, grounded in its knowledge base."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def draft(self, brand: BrandProfile, topic: str, knowledge_base: KnowledgeBase | None = None) -> str:
        knowledge_section = ""
        if knowledge_base is not None:
            context = knowledge_base.context_block(topic)
            if context:
                knowledge_section = (
                    "\nBrand knowledge base (ground facts/tone in this; don't invent claims "
                    f"beyond it):\n{context}\n"
                )

        system = (
            f"You write social media posts for {brand.name}.\n"
            f"Voice: {brand.voice}\n"
            f"Audience: {brand.audience}\n"
            "Guidelines:\n" + "\n".join(f"- {g}" for g in brand.guidelines) + "\n"
            "Never mention or allude to: " + ", ".join(brand.banned_topics) + "."
            + knowledge_section
            + "\nOutput only the post text, nothing else."
        )
        return self._provider.complete(system, f"Write a post about: {topic}").strip()
