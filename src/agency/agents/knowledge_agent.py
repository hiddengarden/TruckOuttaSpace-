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
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Folders that show up inside a real vault but aren't content: Obsidian's
# own config, its trash, and (seen in this project's own discovery output)
# Syncthing's .stversions backup-history folder, which looks like a second
# vault (it has its own .obsidian) but is just version history of the same
# one.
_SKIP_DIR_NAMES = {".obsidian", ".trash", ".stversions", ".git"}
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


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

    def ingest_local_folder(self, customer_slug: str, brand_slug: str, folder: Path | str) -> list[KnowledgeDoc]:
        """Ingests every .md file under `folder` (recursively) into the same
        per-brand corpus URL-scraped pages use -- an Obsidian vault (or any
        folder of markdown) becomes just another knowledge source, read by
        the same KnowledgeBase. Copies content into the corpus rather than
        reading the vault in place, so the corpus stays self-contained and
        portable even if the vault moves.

        Known limitation: only plain markdown image syntax
        `![alt](relative/path.png)` is resolved and copied; Obsidian's own
        `![[wikilink]]` embed syntax is not rewritten (would need
        vault-wide alias resolution, a separate feature) -- content still
        ingests fine, embedded images just won't carry over for those.
        """
        folder = Path(folder)
        if not folder.is_dir():
            # Path.rglob() on a nonexistent directory silently yields
            # nothing rather than raising -- without this check, a typo'd
            # vault path would ingest zero docs with no error at all.
            raise NotADirectoryError(f"{folder} is not a directory")
        brand_dir = self._root / customer_slug / brand_slug
        docs = []
        for md_path in _discover_markdown_files(folder):
            docs.append(self._ingest_local_file(brand_dir, folder, md_path))
        return docs

    def _ingest_local_file(self, brand_dir: Path, vault_root: Path, md_path: Path) -> KnowledgeDoc:
        relative = md_path.relative_to(vault_root)
        raw = md_path.read_text(errors="replace")
        title = _first_heading(raw) or md_path.stem
        slug = _slugify(str(relative.with_suffix(""))) or _slugify(md_path.stem)
        fetched_at = datetime.now(timezone.utc).isoformat()

        pages_dir = brand_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = pages_dir / f"{slug}.md"
        markdown_path.write_text(
            f"---\nsource_url: {md_path}\ntitle: {title}\nfetched_at: {fetched_at}\n---\n\n{raw}"
        )

        asset_paths = self._copy_local_images(raw, md_path.parent, brand_dir / "assets" / slug)

        doc = KnowledgeDoc(
            id=slug,
            source_url=str(md_path),
            title=title,
            markdown_path=str(markdown_path.relative_to(self._root)),
            asset_paths=[str(p.relative_to(self._root)) for p in asset_paths],
            fetched_at=fetched_at,
        )
        self._upsert_manifest(brand_dir, doc)
        return doc

    def _copy_local_images(self, markdown: str, page_dir: Path, assets_dir: Path) -> list[Path]:
        matches = _MD_IMAGE_RE.findall(markdown)[:MAX_IMAGES_PER_PAGE]
        saved: list[Path] = []
        for i, ref in enumerate(matches):
            if ref.startswith(("http://", "https://")):
                continue  # a remote image inside local markdown -- not this method's job
            source = (page_dir / ref).resolve()
            if not source.is_file() or source.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            if source.stat().st_size > MAX_IMAGE_BYTES:
                continue
            assets_dir.mkdir(parents=True, exist_ok=True)
            dest = assets_dir / f"img-{i}{source.suffix.lower()}"
            dest.write_bytes(source.read_bytes())
            saved.append(dest)
        return saved

    def ingest_paperless(
        self,
        customer_slug: str,
        brand_slug: str,
        paperless_client,
        *,
        tag_id: int | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        query: str | None = None,
    ) -> list[KnowledgeDoc]:
        """Pulls already-OCR'd document text from Paperless-ngx into the
        same per-brand corpus URL scraping and local-folder ingestion both
        use. At least one filter should normally be given -- Paperless
        holds one shared archive across everything, not per-brand, so an
        unfiltered call would mix in every other brand's/customer's
        documents too."""
        brand_dir = self._root / customer_slug / brand_slug
        docs = []
        for document in paperless_client.list_documents(
            tag_id=tag_id, correspondent_id=correspondent_id, document_type_id=document_type_id, query=query
        ):
            docs.append(self._ingest_paperless_document(brand_dir, document))
        return docs

    def _ingest_paperless_document(self, brand_dir: Path, document: dict) -> KnowledgeDoc:
        title = document.get("title") or f"document-{document['id']}"
        content = document.get("content", "")
        slug = _slugify(f"paperless-{document['id']}-{title}")
        fetched_at = datetime.now(timezone.utc).isoformat()
        source = f"paperless:document:{document['id']}"

        pages_dir = brand_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = pages_dir / f"{slug}.md"
        markdown_path.write_text(f"---\nsource_url: {source}\ntitle: {title}\nfetched_at: {fetched_at}\n---\n\n{content}")

        doc = KnowledgeDoc(
            id=slug, source_url=source, title=title,
            markdown_path=str(markdown_path.relative_to(self._root)), fetched_at=fetched_at,
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


def _discover_markdown_files(folder: Path) -> list[Path]:
    return sorted(
        p
        for p in folder.rglob("*.md")
        if not any(part in _SKIP_DIR_NAMES for part in p.relative_to(folder).parts)
    )


def _first_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:80]


def _guess_image_ext(content_type: str, url: str) -> str:
    if content_type in _IMAGE_EXT_BY_MIME:
        return _IMAGE_EXT_BY_MIME[content_type]
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".bin"
