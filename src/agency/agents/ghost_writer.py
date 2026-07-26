import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agency.agents.artist import Artist
from agency.agents.designer import Designer
from agency.inference.provider import LLMProvider
from agency.org import BrandContext


@dataclass
class Chapter:
    title: str
    content: str
    illustration_path: Path | None = None


@dataclass
class Publication:
    title: str
    chapters: list[Chapter] = field(default_factory=list)
    cover_image_path: Path | None = None


class GhostWriter:
    """Writes long-form publications (books, courses, ebooks) chapter by chapter.

    Illustrations/covers are optional: pass a Designer + Artist and a list of
    available workflow styles to have GhostWriter ask Designer which style
    fits a given brief, then have Artist render it. Without them, chapters
    are text-only.
    """

    def __init__(self, provider: LLMProvider, designer: Designer | None = None, artist: Artist | None = None):
        self._provider = provider
        self._designer = designer
        self._artist = artist

    def write_outline(self, brand: BrandContext, brief: str, chapter_count: int) -> list[str]:
        system = (
            f"You are a ghost writer producing a table of contents for {brand.name}.\n"
            f"Voice: {brand.voice}\n"
            f"Audience: {brand.audience}\n"
            f"Reply with ONLY a JSON array of exactly {chapter_count} chapter titles, nothing else."
        )
        raw = self._provider.complete(system, f"Publication brief: {brief}").strip()
        try:
            titles = json.loads(raw)
            if not isinstance(titles, list):
                raise ValueError("expected a JSON array")
        except (json.JSONDecodeError, ValueError):
            return [raw]
        return [str(title) for title in titles][:chapter_count]

    def write_chapter(self, brand: BrandContext, brief: str, chapter_title: str) -> str:
        system = (
            f"You are a ghost writer for {brand.name}.\n"
            f"Voice: {brand.voice}\n"
            f"Audience: {brand.audience}\n"
            f"Publication brief: {brief}\n"
            "Write the full chapter in markdown. Output only the chapter content, "
            "starting with a level-2 heading of the chapter title."
        )
        return self._provider.complete(system, f"Write the chapter: {chapter_title}").strip()

    def request_illustration(
        self, brand: BrandContext, brief: str, assets_dir: str | Path, available_styles: list[str]
    ) -> Path | None:
        if self._designer is None or self._artist is None or not available_styles:
            return None
        rec = self._designer.recommend_style(brand, brief, available_styles)
        paths = self._artist.generate(
            brand, brief, assets_dir, style=rec.style, negative_prompt=rec.negative_prompt, checkpoint=rec.checkpoint
        )
        return paths[0] if paths else None

    def write_publication(
        self,
        brand: BrandContext,
        brief: str,
        assets_dir: str | Path,
        chapter_count: int = 5,
        illustrate_chapters: bool = False,
        available_styles: list[str] | None = None,
    ) -> Publication:
        styles = available_styles or []
        cover_image_path = self.request_illustration(brand, f"Cover art for: {brief}", assets_dir, styles)

        chapters = []
        for title in self.write_outline(brand, brief, chapter_count):
            content = self.write_chapter(brand, brief, title)
            illustration_path = None
            if illustrate_chapters:
                illustration_path = self.request_illustration(
                    brand, f"Illustration for the chapter '{title}': {content[:300]}", assets_dir, styles
                )
            chapters.append(Chapter(title=title, content=content, illustration_path=illustration_path))

        return Publication(title=brief, chapters=chapters, cover_image_path=cover_image_path)


def render_publication_markdown(publication: Publication) -> str:
    parts = [f"# {publication.title}\n"]
    if publication.cover_image_path is not None:
        parts.append(f"![cover]({publication.cover_image_path})\n")
    for chapter in publication.chapters:
        parts.append(chapter.content)
        if chapter.illustration_path is not None:
            parts.append(f"![{chapter.title}]({chapter.illustration_path})")
    return "\n\n".join(parts)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:80]
