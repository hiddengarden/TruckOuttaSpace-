import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

MAX_IMAGES_PER_PAGE = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
USER_AGENT = "agency-knowledge-bot/0.1 (+brand knowledge base builder)"

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_IMAGE_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class RobotsDisallowed(Exception):
    pass


@dataclass
class KnowledgeDoc:
    id: str
    source_url: str
    title: str
    markdown_path: str
    asset_paths: list[str] = field(default_factory=list)
    fetched_at: str = ""


class KnowledgeAgent:
    """Scrapes brand-approved URLs into a per-brand markdown + image corpus.

    One corpus per brand under `<knowledge_root>/<customer_slug>/<brand_slug>/`:
      pages/<slug>.md          -- markdown with a small YAML-ish front matter
      assets/<slug>/img-N.ext  -- images found in that page's main content
      manifest.json            -- list of KnowledgeDoc records
    """

    def __init__(self, knowledge_root: Path | str, client: httpx.Client | None = None):
        self._root = Path(knowledge_root)
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True
        )

    def ingest_url(self, customer_slug: str, brand_slug: str, url: str) -> KnowledgeDoc:
        self._check_robots(url)

        response = self._client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        main = soup.find("article") or soup.find("main") or soup.body or soup
        markdown = markdownify(str(main), heading_style="ATX").strip()

        slug = _slugify(title) or _slugify(url)
        fetched_at = datetime.now(timezone.utc).isoformat()
        brand_dir = self._root / customer_slug / brand_slug

        pages_dir = brand_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = pages_dir / f"{slug}.md"
        markdown_path.write_text(
            f"---\nsource_url: {url}\ntitle: {title}\nfetched_at: {fetched_at}\n---\n\n{markdown}"
        )

        asset_paths = self._download_images(main, url, brand_dir / "assets" / slug)

        doc = KnowledgeDoc(
            id=slug,
            source_url=url,
            title=title,
            markdown_path=str(markdown_path.relative_to(self._root)),
            asset_paths=[str(p.relative_to(self._root)) for p in asset_paths],
            fetched_at=fetched_at,
        )
        self._upsert_manifest(brand_dir, doc)
        return doc

    def _check_robots(self, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError:
            return  # robots.txt unreachable -- don't block the fetch on that
        if response.status_code >= 400:
            return  # no robots.txt -> allowed by default
        parser.parse(response.text.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

    def _download_images(self, content_node, page_url: str, assets_dir: Path) -> list[Path]:
        images = content_node.find_all("img", src=True)[:MAX_IMAGES_PER_PAGE]
        saved: list[Path] = []
        if not images:
            return saved

        assets_dir.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate(images):
            image_url = urljoin(page_url, img["src"])
            try:
                response = self._client.get(image_url)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            if len(response.content) > MAX_IMAGE_BYTES:
                continue
            ext = _guess_image_ext(response.headers.get("content-type", ""), image_url)
            path = assets_dir / f"img-{i}{ext}"
            path.write_bytes(response.content)
            saved.append(path)
        return saved

    def _upsert_manifest(self, brand_dir: Path, doc: KnowledgeDoc) -> None:
        path = brand_dir / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else []
        manifest = [entry for entry in manifest if entry["id"] != doc.id]
        manifest.append(doc.__dict__)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2))

    def close(self) -> None:
        self._client.close()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:80]


def _guess_image_ext(content_type: str, url: str) -> str:
    if content_type in _IMAGE_EXT_BY_MIME:
        return _IMAGE_EXT_BY_MIME[content_type]
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".bin"
