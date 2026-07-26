import json

from pydantic import BaseModel

from agency.inference.provider import LLMProvider
from agency.org import BrandContext


class Verdict(BaseModel):
    approved: bool
    reason: str
    revised_text: str | None = None


_SYSTEM_TEMPLATE = (
    "You are the brand-safety supervisor for {name}.\n"
    "Guidelines:\n{guidelines}\n"
    "Banned topics: {banned}\n\n"
    "Review the draft post below. Reply with ONLY a JSON object of this shape:\n"
    '{{"approved": bool, "reason": string, "revised_text": string or null}}\n'
    "Set approved=true only if the draft fully complies with the guidelines and touches "
    "none of the banned topics. If it doesn't comply but is fixable, set approved=false "
    "and put a corrected version in revised_text."
)


class SupervisorAgent:
    """Reviews drafts against brand guidelines before anything reaches Postiz."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def review(self, brand: BrandContext, draft: str) -> Verdict:
        system = _SYSTEM_TEMPLATE.format(
            name=brand.name,
            guidelines="\n".join(f"- {g}" for g in brand.guidelines),
            banned=", ".join(brand.banned_topics),
        )
        raw = self._provider.complete(system, draft).strip()
        try:
            return Verdict.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return Verdict(approved=False, reason=f"Supervisor returned unparseable output: {raw!r}")
