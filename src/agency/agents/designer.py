import json
from pathlib import Path

from pydantic import BaseModel

from agency.inference.provider import LLMProvider
from agency.org import BrandContext

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class StyleRecommendation(BaseModel):
    style: str
    checkpoint: str | None = None
    negative_prompt: str = ""


class CreativeVerdict(BaseModel):
    approved: bool
    reason: str


_STYLE_SYSTEM_TEMPLATE = (
    "You are the creative director for {name}.\n"
    "Voice: {voice}\n"
    "Audience: {audience}\n"
    "Available visual styles/workflows: {styles}\n"
    "Reply with ONLY a JSON object of this shape:\n"
    '{{"style": string (one of the available styles), "checkpoint": string or null, '
    '"negative_prompt": string}}'
)

_REVIEW_SYSTEM_TEMPLATE = (
    "You are the creative quality-control reviewer for {name}.\n"
    "Voice: {voice}\n"
    "Audience: {audience}\n"
    "The asset was generated for this brief: {brief}\n"
    "Judge whether it matches the brand's voice, audience, and personality.\n"
    'Reply with ONLY a JSON object: {{"approved": bool, "reason": string}}'
)


class Designer:
    """Creative QC gate and the 'intermediate entity' between content agents
    and the render agents (Artist/VideoMaster): decides which style/model to
    use for a brief, and reviews the resulting asset against brand voice/
    audience/personality before it's considered usable.

    review_asset() needs a vision-capable model configured on the provider
    (e.g. Ollama's llava) -- if none is configured, the underlying HTTP call
    fails and that failure propagates rather than silently approving.
    """

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def recommend_style(self, brand: BrandContext, brief: str, available_styles: list[str]) -> StyleRecommendation:
        system = _STYLE_SYSTEM_TEMPLATE.format(
            name=brand.name, voice=brand.voice, audience=brand.audience, styles=", ".join(available_styles)
        )
        raw = self._provider.complete(system, f"Recommend visual parameters for: {brief}").strip()
        try:
            return StyleRecommendation.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return StyleRecommendation(style=available_styles[0])

    def review_asset(self, brand: BrandContext, brief: str, asset_path: str | Path) -> CreativeVerdict:
        path = Path(asset_path)
        system = _REVIEW_SYSTEM_TEMPLATE.format(name=brand.name, voice=brand.voice, audience=brand.audience, brief=brief)
        raw = self._provider.complete_with_image(
            system, "Review this asset.", path.read_bytes(), _guess_mime(path)
        ).strip()
        try:
            return CreativeVerdict.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return CreativeVerdict(approved=False, reason=f"Designer returned unparseable output: {raw!r}")


def _guess_mime(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")
