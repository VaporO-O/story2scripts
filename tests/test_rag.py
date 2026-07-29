import json

import httpx
import pytest

from story2script.llm_client import LLMClient
from story2script.metrics import metrics
from story2script.parser import parse_chapters
from story2script.rag import (
    build_story_knowledge,
    rag_top_k,
)
from story2script.story_state import extract_global_story_state

NOVEL = (
    "第一章 出发\n林夏背起行囊，在渡口告别母亲。\n乌篷船缓缓驶向三江口。\n"
    "第二章 风暴\n夜里风暴袭来，船夫周大河把稳船舵。\n林夏抱紧木箱，箱中是父亲的遗物。\n"
    "第三章 灯塔\n黎明时分，灯塔的守塔人收留了落难的林夏。\n林夏决定继续北上。"
)


def build_demo_knowledge():
    chapters = parse_chapters(NOVEL)
    return build_story_knowledge(chapters, extract_global_story_state(chapters))


def configure_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("AI_EMBED_MODEL", "test-embed")


def keyword_vector(text: str) -> list[float]:
    return [float(text.count(keyword)) for keyword in ("行囊", "风暴", "灯塔")]


def embeddings_response(vectors: list[list[float]], usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": index, "embedding": vector} for index, vector in enumerate(vectors)
            ],
            "usage": usage or {},
        },
    )


def embedding_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content.decode("utf-8"))
    return embeddings_response(
        [keyword_vector(text) for text in payload["input"]],
        usage={"prompt_tokens": 7},
    )


# ---------------------------------------------------------------- 词法检索


def test_build_story_knowledge_stats() -> None:
    knowledge = build_demo_knowledge()
    stats = knowledge.stats()

    assert stats["retriever"] == "lexical"
    assert stats["doc_count"] >= 6
    assert stats["by_kind"]["chunk"] >= 3
    assert stats["by_kind"]["event"] == 3


def test_lexical_search_hits_relevant_chapter() -> None:
    knowledge = build_demo_knowledge()

    hits = knowledge.search("木箱里父亲的遗物", kinds=("chunk",))

    assert hits
    assert hits[0]["chapter"] == "第二章 风暴"
    assert "木箱" in hits[0]["snippet"]
    assert hits == knowledge.search("木箱里父亲的遗物", kinds=("chunk",))


def test_search_irrelevant_query_returns_empty() -> None:
    knowledge = build_demo_knowledge()

    assert knowledge.search("zzzz qqqq") == []


def test_before_chapter_blocks_future_content() -> None:
    knowledge = build_demo_knowledge()

    future_hits = knowledge.search("灯塔守塔人", before_chapter=3, kinds=("chunk", "event"))
    assert all(hit["chapter"] != "第三章 灯塔" for hit in future_hits)

    first_chunk_hits = knowledge.search("灯塔", before_chapter=1, kinds=("chunk", "event"))
    assert first_chunk_hits == []


def test_kinds_filter_limits_doc_types() -> None:
    knowledge = build_demo_knowledge()

    hits = knowledge.search("林夏", kinds=("character",))

    assert all(hit["kind"] == "character" for hit in hits)


def test_rag_top_k_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TOP_K", "1")
    assert rag_top_k() == 1

    knowledge = build_demo_knowledge()
    assert len(knowledge.search("林夏")) <= 1

    monkeypatch.setenv("RAG_TOP_K", "abc")
    with pytest.raises(ValueError, match="RAG_TOP_K"):
        rag_top_k()


# ---------------------------------------------------------------- Embedding


def test_llm_client_embed_batches_and_records_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_embeddings(monkeypatch)
    monkeypatch.setenv("AI_EMBED_BATCH_SIZE", "2")
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert str(request.url) == "https://example.test/v1/embeddings"
        assert payload["model"] == "test-embed"
        requests.append(payload["input"])
        return embedding_handler(request)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    vectors = client.embed(["一", "二", "三", "四", "五"])

    assert len(vectors) == 5
    assert [len(batch) for batch in requests] == [2, 2, 1]
    row = metrics.summary()["llm"]["AI embeddings"]
    assert row["calls"] == 3
    assert row["success"] == 3
    assert row["prompt_tokens"] == 21


def test_llm_client_embed_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_embeddings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="HTTP 500"):
        client.embed(["文本"])
    assert metrics.summary()["llm"]["AI embeddings"]["failure"] == 1

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": []}]})

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(malformed)))
    with pytest.raises(ValueError, match="malformed"):
        client.embed(["文本"])


def test_llm_client_embed_requires_embed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("AI_EMBED_MODEL", raising=False)

    client = LLMClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    with pytest.raises(ValueError, match="AI_EMBED_MODEL"):
        client.embed(["文本"])


def test_embedding_retriever_ranks_by_semantic_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_embeddings(monkeypatch)
    chapters = parse_chapters(NOVEL)
    knowledge = build_story_knowledge(
        chapters,
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(embedding_handler)),
    )

    assert knowledge.retriever_kind == "embedding"
    hits = knowledge.search("夜里风暴来了", kinds=("chunk",))
    assert hits
    assert hits[0]["chapter"] == "第二章 风暴"


def test_embedding_falls_back_to_lexical_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("AI_EMBED_MODEL", raising=False)

    knowledge = build_story_knowledge(
        parse_chapters(NOVEL),
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: None)),
    )

    assert knowledge.retriever_kind == "lexical"


def test_embedding_index_failure_falls_back_to_lexical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_embeddings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    knowledge = build_story_knowledge(
        parse_chapters(NOVEL),
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert knowledge.retriever_kind == "lexical"
    assert knowledge.search("林夏")


def test_embedding_query_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_embeddings(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return embedding_handler(request)
        return httpx.Response(500)

    knowledge = build_story_knowledge(
        parse_chapters(NOVEL),
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert knowledge.retriever_kind == "embedding"

    assert knowledge.search("风暴") == []
    query_row = metrics.summary()["tasks"]["rag_query"]
    assert query_row["failure"] >= 1


# ---------------------------------------------------------------- 埋点


def test_rag_index_and_query_tasks_recorded() -> None:
    knowledge = build_demo_knowledge()
    knowledge.search("林夏", top_k=2)

    tasks = metrics.summary()["tasks"]
    assert tasks["rag_index"]["success"] >= 1
    assert tasks["rag_query"]["success"] >= 1

    index_event = next(
        event for event in metrics.recent_events() if event.get("kind") == "rag_index"
    )
    assert index_event["extra"]["retriever"] == "lexical"
    assert index_event["extra"]["docs"] >= 6
    query_event = next(
        event for event in metrics.recent_events() if event.get("kind") == "rag_query"
    )
    assert query_event["extra"]["top_k"] == 2


# ---------------------------------------------------------------- REST API


def test_rag_query_api() -> None:
    from fastapi.testclient import TestClient

    from story2script.main import app

    client = TestClient(app)
    response = client.post(
        "/api/rag/query", json={"novel_text": NOVEL, "query": "夜里的风暴", "top_k": 2}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["retriever"] == "lexical"
    assert data["stats"]["doc_count"] > 0
    assert data["hits"]
    assert len(data["hits"]) <= 2

    bad = client.post("/api/rag/query", json={"novel_text": "第一章 一\n内容", "query": "x"})
    assert bad.status_code == 422
    assert "RAG 查询失败" in bad.json()["detail"]
