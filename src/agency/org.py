from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Project(BaseModel):
    """A sub-brand layer: a theme, campaign, initiative, or series within a brand."""

    slug: str
    name: str
    kind: str = "campaign"
    topic_hint: str | None = None
    extra_guidelines: list[str] = Field(default_factory=list)
    extra_banned_topics: list[str] = Field(default_factory=list)
    posts_per_run: int | None = None
    due_date: str | None = None  # ISO date (YYYY-MM-DD); checked by Secretary
    active: bool = True  # set false to pause this project without touching the brand


class WordPressConfig(BaseModel):
    """Optional website-publishing target for a brand. `app_password_env`
    names the .env variable holding the Application Password -- the secret
    itself is never stored in this YAML (same reasoning as POSTIZ_API_KEY
    living in .env, not brands/*.yaml)."""

    base_url: str
    username: str
    app_password_env: str


class Brand(BaseModel):
    slug: str
    name: str
    postiz_group_id: str
    integration_ids: list[str] = Field(min_length=1)
    voice: str
    audience: str
    guidelines: list[str] = Field(default_factory=list)
    banned_topics: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    posts_per_run: int = Field(default=1, ge=1)
    projects: list[Project] = Field(default_factory=list)
    wordpress: WordPressConfig | None = None
    # Local-only by default: a brand only ever reaches OpenRouter if this is
    # explicitly set true. Keeps "local-first for privacy" a real per-brand
    # decision instead of a silent, inherited default across the whole system.
    allow_cloud_fallback: bool = False
    # Set false to pause this brand entirely -- run-all/loop skip it, freeing
    # its share of scheduled-run time for other brands without deleting or
    # commenting out its config.
    active: bool = True


class Customer(BaseModel):
    slug: str
    name: str
    brands: list[Brand] = Field(default_factory=list)


def load_customer(path: str | Path) -> Customer:
    data = yaml.safe_load(Path(path).read_text())
    return Customer.model_validate(data)


def discover_customers(org_dir: str | Path) -> list[Customer]:
    return [load_customer(path) for path in sorted(Path(org_dir).glob("*.yaml"))]


def find_brand(customer: Customer, brand_slug: str) -> Brand:
    for brand in customer.brands:
        if brand.slug == brand_slug:
            return brand
    raise ValueError(f"No brand '{brand_slug}' in customer '{customer.slug}'")


def find_project(brand: Brand, project_slug: str) -> Project:
    for project in brand.projects:
        if project.slug == project_slug:
            return project
    raise ValueError(f"No project '{project_slug}' in brand '{brand.slug}'")


@dataclass(frozen=True)
class BrandContext:
    """The effective voice/guardrails agents draft against: a brand, optionally
    narrowed by one of its projects (a campaign/theme/series)."""

    name: str
    voice: str
    audience: str
    guidelines: list[str]
    banned_topics: list[str]
    topic_hint: str | None = None


def brand_context(brand: Brand, project: Project | None = None) -> BrandContext:
    if project is None:
        return BrandContext(
            name=brand.name,
            voice=brand.voice,
            audience=brand.audience,
            guidelines=brand.guidelines,
            banned_topics=brand.banned_topics,
        )
    return BrandContext(
        name=f"{brand.name} / {project.name}",
        voice=brand.voice,
        audience=brand.audience,
        guidelines=brand.guidelines + project.extra_guidelines,
        banned_topics=brand.banned_topics + project.extra_banned_topics,
        topic_hint=project.topic_hint,
    )


def effective_posts_per_run(brand: Brand, project: Project | None) -> int:
    if project is not None and project.posts_per_run is not None:
        return project.posts_per_run
    return brand.posts_per_run
