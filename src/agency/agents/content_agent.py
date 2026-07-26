from agency.brand import BrandProfile
from agency.inference.provider import LLMProvider


class ContentAgent:
    """Drafts social post copy in a brand's voice."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def draft(self, brand: BrandProfile, topic: str) -> str:
        system = (
            f"You write social media posts for {brand.name}.\n"
            f"Voice: {brand.voice}\n"
            f"Audience: {brand.audience}\n"
            "Guidelines:\n" + "\n".join(f"- {g}" for g in brand.guidelines) + "\n"
            "Never mention or allude to: " + ", ".join(brand.banned_topics) + ".\n"
            "Output only the post text, nothing else."
        )
        return self._provider.complete(system, f"Write a post about: {topic}").strip()
