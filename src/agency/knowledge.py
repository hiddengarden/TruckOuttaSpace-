import json
import re
from dataclasses import dataclass
from pathlib import Path

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class RetrievedDoc:
    title: str
    source_url: str
    content: str
    score: int


class KnowledgeBase:
    """Lexical (keyword-overlap) retrieval over a brand's scraped corpus.

    No embeddings/vector store for the MVP -- just word-overlap scoring
    against the manifest built by KnowledgeAgent. Good enough to surface a
    brand's own pages when a topic mentions the same nouns; a real
    similarity search is a roadmap item, not a blocker for the slice.
    """

    def __init__(self, customer_slug: str, brand_slug: str, knowledge_root: Path | str):
        self._root = Path(knowledge_root)
        self._manifest = self._load_manifest(customer_slug, brand_slug)

    def _load_manifest(self, customer_slug: str, brand_slug: str) -> list[dict]:
        path = self._root / customer_slug / brand_slug / "manifest.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())

    def retrieve(self, topic: str, k: int = 3) -> list[RetrievedDoc]:
        query_words = set(_WORD_RE.findall(topic.lower()))
        if not query_words:
            return []

        scored: list[RetrievedDoc] = []
        for entry in self._manifest:
            markdown_path = self._root / entry["markdown_path"]
            if not markdown_path.exists():
                continue
            content = markdown_path.read_text()
            doc_words = _WORD_RE.findall((content + " " + entry["title"]).lower())
            score = sum(1 for word in doc_words if word in query_words)
            if score > 0:
                scored.append(
                    RetrievedDoc(
                        title=entry["title"], source_url=entry["source_url"], content=content, score=score
                    )
                )

        scored.sort(key=lambda doc: doc.score, reverse=True)
        return scored[:k]

    def titles(self) -> list[str]:
        return [entry["title"] for entry in self._manifest]

    def context_block(self, topic: str, k: int = 3, max_chars: int = 1000) -> str:
        docs = self.retrieve(topic, k)
        if not docs:
            return ""
        return "\n\n".join(f"### {doc.title} ({doc.source_url})\n{doc.content[:max_chars]}" for doc in docs)
