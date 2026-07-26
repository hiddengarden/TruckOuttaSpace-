from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class BrandProfile(BaseModel):
    slug: str
    name: str
    postiz_group_id: str
    integration_ids: list[str] = Field(min_length=1)
    voice: str
    audience: str
    guidelines: list[str] = Field(default_factory=list)
    banned_topics: list[str] = Field(default_factory=list)


def load_brand(path: str | Path) -> BrandProfile:
    data = yaml.safe_load(Path(path).read_text())
    return BrandProfile.model_validate(data)
