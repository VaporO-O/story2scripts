import json
from pathlib import Path

import httpx
import pytest

from story2script.evaluation.retrieval import RetrievalDataset, score_ranking
from story2script.parser import parse_chapters
from story2script.rag import (
    EmbeddingRetriever, HybridRetriever, KnowledgeDoc, LexicalRetriever, StoryKnowledgeBase,
    build_story_knowledge,
)
from story2script.story_qa import answer_story_question


NOVEL = ('第一章 钥匙\n林夏把铜钥匙藏在蓝色茶罐中。\n'
         '第二章 仓库\n周宁在仓库找到失踪的账本。\n'
         '第三章 回家\n陈默修好无线电后回到渡口。')


def configure(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test")
    monkeypatch.setenv("AI_EMBED_MODEL", "test-embed")


def test_hybrid_keeps_rare_names_when_semantic_ranking_is_flat():
    class Semantic:
        def scores(self, query):
            return [0.5, 0.5, 0.5]
    docs = [KnowledgeDoc(str(i), "chunk", i + 1, str(i), text) for i, text in enumerate(
        ["仓库中的钥匙", "周宁拿起钥匙", "林夏的铜钥匙藏在茶罐里"])]
    lexical = LexicalRetriever()
    lexical.index([doc.text for doc in docs])
    knowledge = StoryKnowledgeBase(docs, HybridRetriever(lexical, Semantic()))
    assert knowledge.search("林夏铜钥匙茶罐", top_k=1)[0]["doc_id"] == "2"


def test_hybrid_query_failure_uses_lexical(monkeypatch):
    configure(monkeypatch)
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls > 1:
            return httpx.Response(503)
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1., 0.]}
                                                for i in range(len(texts))]})
    knowledge = build_story_knowledge(parse_chapters(NOVEL), mode="ai", retrieval="hybrid",
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert knowledge.retriever_kind == "hybrid"
    assert knowledge.search("铜钥匙茶罐")[0]["chapter_index"] == 1


def test_snippet_contains_evidence_near_end():
    text = "外面下着雨。" * 65 + "铜钥匙藏在蓝色茶罐。"
    lexical = LexicalRetriever()
    lexical.index([text])
    knowledge = StoryKnowledgeBase([KnowledgeDoc("a", "chunk", 1, "第一章", text)], lexical)
    hit = knowledge.search("铜钥匙藏哪里蓝色茶罐")[0]
    assert "铜钥匙" in hit["snippet"]
    assert hit["snippet"] == text[hit["snippet_start"]:hit["snippet_end"]]


def test_before_chapter_excludes_full_novel_facts():
    from story2script.story_state import extract_global_story_state
    chapters = parse_chapters(NOVEL)
    knowledge = build_story_knowledge(chapters, extract_global_story_state(chapters))
    assert all(hit["kind"] == "chunk" or hit["kind"] == "event"
               for hit in knowledge.search("林夏", before_chapter=2))
    assert knowledge.search("铜钥匙", before_chapter=1) == []


def test_reranker_cannot_reintroduce_future_evidence():
    class Reranker:
        def complete_json(self, prompt, **kwargs):
            assert "未来秘密" not in prompt
            return json.dumps({"doc_ids": ["future"]})
    docs = [KnowledgeDoc("past", "chunk", 1, "第一章", "铜钥匙在茶罐中"),
            KnowledgeDoc("future", "chunk", 3, "第三章", "铜钥匙的未来秘密")]
    lexical = LexicalRetriever()
    lexical.index([doc.text for doc in docs])
    knowledge = StoryKnowledgeBase(docs, lexical, Reranker())
    hits = knowledge.search("铜钥匙", before_chapter=2, rerank=True)
    assert [hit["doc_id"] for hit in hits] == ["past"]
    assert hits[0]["rerank_status"] == "failed_using_retrieval_order"


def test_embedding_dimension_mismatch_fails():
    class Embedder:
        def embed(self, texts):
            return [[1., 0.]] if len(texts) == 1 else [[1., 0., 0.]] * len(texts)
    retriever = EmbeddingRetriever(Embedder())
    retriever.index(["a", "b"])
    with pytest.raises(ValueError, match="维度"):
        retriever.scores("c")


@pytest.mark.parametrize("citation,abstained", [
    ({"doc_id": "ch1-chunk1", "quote": "铜钥匙藏在蓝色茶罐中"}, False),
    ({"doc_id": "invented", "quote": "铜钥匙藏在蓝色茶罐中"}, True),
    ({"doc_id": "ch1-chunk1", "quote": "铜钥匙藏在红色盒子里"}, True),
])
def test_answers_require_literal_retrieved_citations(monkeypatch, citation, abstained):
    configure(monkeypatch)
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "answer": "铜钥匙藏在蓝色茶罐中。", "abstained": False, "citations": [citation],
        })}}]})
    answer = answer_story_question(NOVEL, "铜钥匙藏在哪里？", mode="ai", retrieval="lexical",
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert answer["abstained"] is abstained
    assert bool(answer["citations"]) is (not abstained)


def test_local_qa_and_empty_scope_do_not_call_model():
    result = answer_story_question(NOVEL, "铜钥匙")
    assert result["answer_kind"] == "excerpts"
    assert result["citations"][0]["chapter"] == "第一章 钥匙"
    assert answer_story_question(NOVEL, "铜钥匙", before_chapter=1)["abstained"]
    result = answer_story_question(NOVEL, "铜钥匙藏在哪里？")
    assert len(result["hits"]) == 1  # a shared 在 must not retrieve an unrelated chapter


def test_retrieval_metrics_and_dataset_independence():
    row = score_ranking([{"doc_id": "x"}, {"doc_id": "b"}], {"a", "b"}, 5)
    assert row["recall"] == 0.5 and row["reciprocal_rank"] == 0.5
    assert score_ranking([], set(), 5)["recall"] is None
    root = Path(__file__).parents[1] / "evals/datasets/rag/v1"
    datasets = [RetrievalDataset.model_validate_json((root / f"{split}.json").read_text(encoding="utf-8"))
                for split in ("dev", "holdout")]
    assert not {c.id for c in datasets[0].cases} & {c.id for c in datasets[1].cases}
    assert sum(len(case.questions) for dataset in datasets for case in dataset.cases) == 72


def test_qa_endpoint_returns_evidence_and_rejects_invalid_scope():
    from fastapi.testclient import TestClient
    from story2script.main import app
    client = TestClient(app)
    response = client.post("/api/rag/answer", json={"novel_text": NOVEL, "query": "铜钥匙"})
    assert response.status_code == 200
    assert response.json()["citations"]
    assert client.post("/api/rag/answer", json={"novel_text": NOVEL, "query": "铜钥匙",
                                                "before_chapter": 99}).status_code == 422


def test_rag_cli_gates_and_unavailable_variants(tmp_path):
    from story2script.evaluation.__main__ import main
    dataset = str(Path(__file__).parents[1] / "evals/datasets/rag/v1/dev.json")
    common = ["rag", "--dataset", dataset, "--output", str(tmp_path / "rag")]
    assert main([*common, "--min-recall", "0.9"]) == 0
    assert main([*common, "--min-mrr", "1"]) == 1
    assert main([*common, "--variants", "hybrid"]) == 1
    report = json.loads((tmp_path / "rag.json").read_text(encoding="utf-8"))
    assert report["summary"]["hybrid"]["status"] == "unavailable"
    assert report["questions"] == []
