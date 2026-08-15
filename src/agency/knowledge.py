import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agency.inference.embeddings import EmbeddingProvider, cosine_similarity
from agency.inference.provider import LLMProvider

_WORD_RE = re.compile(r"[a-z0-9]+")

_RERANK_SYSTEM = (
    "You rank candidate knowledge-base passages by relevance to a topic. "
    "Return strict JSON: {\"ranked_ids\": [\"id1\", \"id2\", ...]} listing "
    "every candidate id given, most relevant to the topic first. Do not "
    "invent ids, do not omit any of the given ids, no prose outside the JSON."
)


class _RerankOrder(BaseModel):
    ranked_ids: list[str] = []


@dataclass
class RetrievedDoc:
    title: str
    source_url: str
    content: str
    score: float
    id: str = ""


class KnowledgeBase:
    """Retrieval over a brand's scraped corpus.

    Three modes, in order of what's configured -- each degrades gracefully
    to the one below it rather than failing the whole compose:
    1. Embeddings + LLM rerank (both `embedding_provider` and
       `reranker_provider` given): embed the topic, cosine-similarity
       against every doc with a stored embedding (see KnowledgeAgent) for
       a candidate shortlist, then ask a local LLM to reorder that
       shortlist by actual relevance -- a bi-encoder-then-reranker pattern,
       needed because keyword overlap alone surfaces the wrong note often
       once a corpus gets into the thousands of notes (an Obsidian vault,
       say) and every downstream stage inherits that error.
    2. Embeddings only (`embedding_provider` given, no reranker): cosine
       similarity order, no LLM rerank pass.
    3. Keyword overlap (nothing configured, or embeddings/rerank fail at
       runtime, or a doc has no stored embedding yet): the original MVP
       behavior, unchanged. Also what every doc without an embedding on
       file falls back to individually, e.g. right after upgrading before
       a full re-ingest has run.
    """

    def __init__(
        self,
        customer_slug: str,
        brand_slug: str,
        knowledge_root: Path | str,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: LLMProvider | None = None,
        rerank_candidates: int = 10,
    ):
        self._root = Path(knowledge_root)
        self._manifest = self._load_manifest(customer_slug, brand_slug)
        self._embeddings = self._load_embeddings(customer_slug, brand_slug)
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._rerank_candidates = rerank_candidates

    def _load_manifest(self, customer_slug: str, brand_slug: str) -> list[dict]:
        path = self._root / customer_slug / brand_slug / "manifest.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())

    def _load_embeddings(self, customer_slug: str, brand_slug: str) -> dict[str, list[float]]:
        path = self._root / customer_slug / brand_slug / "embeddings.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def retrieve(self, topic: str, k: int = 3) -> list[RetrievedDoc]:
        if self._embedding_provider is not None and self._embeddings:
            try:
                return self._retrieve_via_embeddings(topic, k)
            except Exception:
                pass  # best-effort, same principle as ingest-time embedding: fall back, don't fail the draft
        return self._retrieve_via_keywords(topic, k)

    def _retrieve_via_embeddings(self, topic: str, k: int) -> list[RetrievedDoc]:
        query_embedding = self._embedding_provider.embed(topic)

        candidates: list[RetrievedDoc] = []
        for entry in self._manifest:
            doc_embedding = self._embeddings.get(entry["id"])
            if doc_embedding is None:
                continue
            markdown_path = self._root / entry["markdown_path"]
            if not markdown_path.exists():
                continue
            candidates.append(
                RetrievedDoc(
                    id=entry["id"],
                    title=entry["title"],
                    source_url=entry["source_url"],
                    content=markdown_path.read_text(),
                    score=cosine_similarity(query_embedding, doc_embedding),
                )
            )
        if not candidates:
            return []

        candidates.sort(key=lambda doc: doc.score, reverse=True)
        shortlist = candidates[: self._rerank_candidates]

        if self._reranker_provider is not None:
            try:
                shortlist = _rerank(self._reranker_provider, topic, shortlist)
            except Exception:
                pass  # keep embedding-similarity order if rerank itself fails
        return shortlist[:k]

    def _retrieve_via_keywords(self, topic: str, k: int) -> list[RetrievedDoc]:
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
                        id=entry["id"], title=entry["title"], source_url=entry["source_url"], content=content,
                        score=float(score),
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


def _rerank(provider: LLMProvider, topic: str, candidates: list[RetrievedDoc]) -> list[RetrievedDoc]:
    by_id = {doc.id: doc for doc in candidates}
    listing = "\n\n".join(f"[{doc.id}] {doc.title}\n{doc.content[:300]}" for doc in candidates)
    user = f"Topic: {topic}\n\nCandidates:\n\n{listing}"

    raw = provider.complete(_RERANK_SYSTEM, user)
    order = _RerankOrder.model_validate(json.loads(raw))

    ranked = [by_id[doc_id] for doc_id in order.ranked_ids if doc_id in by_id]
    seen = {doc.id for doc in ranked}
    ranked.extend(doc for doc in candidates if doc.id not in seen)  # model omitted these -- keep, just last
    return ranked
