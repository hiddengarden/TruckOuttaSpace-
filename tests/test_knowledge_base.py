import json

from agency.knowledge import KnowledgeBase

_CUSTOMER = "example-customer"
_BRAND = "example-brand"


def _write_doc(root, doc_id, title, content, source_url="https://example.test/x"):
    pages_dir = root / _CUSTOMER / _BRAND / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = pages_dir / f"{doc_id}.md"
    markdown_path.write_text(content)

    manifest_path = root / _CUSTOMER / _BRAND / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest.append(
        {
            "id": doc_id,
            "source_url": source_url,
            "title": title,
            "markdown_path": str(markdown_path.relative_to(root)),
            "asset_paths": [],
            "fetched_at": "2026-07-26T00:00:00+00:00",
        }
    )
    manifest_path.write_text(json.dumps(manifest))


def test_retrieve_ranks_by_keyword_overlap(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets for offices.")
    _write_doc(tmp_path, "unrelated", "Company Holidays", "The office is closed in July for the season.")

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path)
    results = kb.retrieve("durable widgets for small offices", k=2)

    assert [doc.title for doc in results][0] == "Our Widgets"
    assert results[0].score > results[1].score


def test_retrieve_returns_empty_when_no_corpus(tmp_path):
    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path)
    assert kb.retrieve("anything") == []
    assert kb.context_block("anything") == ""


def test_context_block_includes_source_attribution(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets.", source_url="https://example.test/widgets")

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path)
    block = kb.context_block("widgets")

    assert "Our Widgets" in block
    assert "https://example.test/widgets" in block
    assert "durable widgets" in block


# --- embeddings + rerank ---


def _write_embedding(root, doc_id, vector):
    path = root / _CUSTOMER / _BRAND / "embeddings.json"
    embeddings = json.loads(path.read_text()) if path.exists() else {}
    embeddings[doc_id] = vector
    path.write_text(json.dumps(embeddings))


class FakeEmbeddingProvider:
    def __init__(self, vectors_by_text):
        self._vectors_by_text = vectors_by_text
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return self._vectors_by_text[text]


class RaisingEmbeddingProvider:
    def embed(self, text):
        raise ConnectionError("ollama unreachable")


class FakeRerankProvider:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self._response

    def complete_with_image(self, *a, **k):
        raise NotImplementedError


def test_retrieve_via_embeddings_orders_by_cosine_similarity(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets.")
    _write_doc(tmp_path, "unrelated", "Company Holidays", "Office closed in July.")
    _write_embedding(tmp_path, "widgets", [1.0, 0.0])
    _write_embedding(tmp_path, "unrelated", [0.0, 1.0])
    embedder = FakeEmbeddingProvider({"durable widgets": [1.0, 0.0]})

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder)
    results = kb.retrieve("durable widgets", k=2)

    assert [doc.title for doc in results] == ["Our Widgets", "Company Holidays"]
    assert results[0].score > results[1].score
    assert embedder.calls == ["durable widgets"]


def test_retrieve_falls_back_to_keywords_when_no_embeddings_file(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets for offices.")
    embedder = FakeEmbeddingProvider({})  # never called -- no embeddings.json to match against

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder)
    results = kb.retrieve("durable widgets", k=1)

    assert results[0].title == "Our Widgets"
    assert embedder.calls == []


def test_retrieve_falls_back_to_keywords_when_embedding_call_fails(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets for offices.")
    _write_embedding(tmp_path, "widgets", [1.0, 0.0])

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=RaisingEmbeddingProvider())
    results = kb.retrieve("durable widgets", k=1)  # must not raise

    assert results[0].title == "Our Widgets"  # keyword fallback still finds it


def test_retrieve_skips_docs_with_no_stored_embedding(tmp_path):
    _write_doc(tmp_path, "widgets", "Our Widgets", "We sell durable widgets.")
    _write_doc(tmp_path, "no-embedding", "Never Embedded", "Some other content.")
    _write_embedding(tmp_path, "widgets", [1.0, 0.0])
    embedder = FakeEmbeddingProvider({"topic": [1.0, 0.0]})

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder)
    results = kb.retrieve("topic", k=5)

    assert [doc.title for doc in results] == ["Our Widgets"]


def test_retrieve_with_reranker_reorders_the_embedding_shortlist(tmp_path):
    _write_doc(tmp_path, "a", "Doc A", "content a")
    _write_doc(tmp_path, "b", "Doc B", "content b")
    # Embedding similarity would rank A first (closer vector)...
    _write_embedding(tmp_path, "a", [1.0, 0.0])
    _write_embedding(tmp_path, "b", [0.9, 0.1])
    embedder = FakeEmbeddingProvider({"topic": [1.0, 0.0]})
    # ...but the reranker says B is actually more relevant.
    reranker = FakeRerankProvider(response=json.dumps({"ranked_ids": ["b", "a"]}))

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder, reranker_provider=reranker)
    results = kb.retrieve("topic", k=2)

    assert [doc.title for doc in results] == ["Doc B", "Doc A"]
    assert len(reranker.calls) == 1


def test_rerank_appends_ids_the_model_omitted_rather_than_dropping_them(tmp_path):
    _write_doc(tmp_path, "a", "Doc A", "content a")
    _write_doc(tmp_path, "b", "Doc B", "content b")
    _write_embedding(tmp_path, "a", [1.0, 0.0])
    _write_embedding(tmp_path, "b", [0.9, 0.1])
    embedder = FakeEmbeddingProvider({"topic": [1.0, 0.0]})
    reranker = FakeRerankProvider(response=json.dumps({"ranked_ids": ["b"]}))  # omits "a"

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder, reranker_provider=reranker)
    results = kb.retrieve("topic", k=2)

    assert [doc.title for doc in results] == ["Doc B", "Doc A"]  # a still present, just last


def test_retrieve_falls_back_to_embedding_order_when_rerank_response_unparseable(tmp_path):
    _write_doc(tmp_path, "a", "Doc A", "content a")
    _write_embedding(tmp_path, "a", [1.0, 0.0])
    embedder = FakeEmbeddingProvider({"topic": [1.0, 0.0]})
    reranker = FakeRerankProvider(response="not json at all")

    kb = KnowledgeBase(_CUSTOMER, _BRAND, tmp_path, embedding_provider=embedder, reranker_provider=reranker)
    results = kb.retrieve("topic", k=1)  # must not raise

    assert results[0].title == "Doc A"
