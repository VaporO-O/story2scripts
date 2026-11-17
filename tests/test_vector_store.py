import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from story2script.main import app
from story2script.parser import parse_chapters
from story2script.rag import KnowledgeDoc, build_story_knowledge
from story2script.story_state import extract_global_story_state
from story2script.vector_store import (
    PersistentVectorIndex,
    database_path,
    delete_index,
    digest,
    list_indexes,
)


class Embedder:
    base_url = "https://embedding.test/v1"
    api_key = "test-private-key"
    embed_model = "test-vector-v1"

    def __init__(self, *, fail=False, dimension=4):
        self.calls = []
        self.fail = fail
        self.dimension = dimension
        self.lock = threading.Lock()

    def embed(self, texts):
        with self.lock:
            self.calls.append(list(texts))
        if self.fail:
            raise ValueError("模拟 Embedding 服务失败")
        return [
            (
                [float(text.count(w)) for w in ("钥匙", "仓库", "茶罐", "雨")]
                + [0.0] * self.dimension
            )[: self.dimension]
            for text in texts
        ]


NOVEL = "第一章 钥匙\n铜钥匙藏在茶罐。\n第二章 仓库\n周宁走进仓库。\n第三章 雨\n林夏站在雨里。"


def build(client=None, novel=NOVEL, **kwargs):
    return build_story_knowledge(
        parse_chapters(novel),
        mode="ai",
        llm_client=client or Embedder(),
        retrieval=kwargs.pop("retrieval", "embedding"),
        **kwargs,
    )


def test_real_sqlite_vec_query_and_reopen_without_reembedding():
    first = Embedder()
    kb = build(first)
    assert kb.stats()["storage"]["backend"] == "sqlite-vec"
    assert not kb.stats()["storage"]["reused"]
    assert kb.search("钥匙茶罐", kinds=("chunk",))[0]["chapter_index"] == 1
    assert len(first.calls) == 2
    second = Embedder()
    reopened = build(second)
    assert reopened.stats()["storage"]["reused"] and second.calls == []
    assert reopened.search("仓库", kinds=("chunk",))[0]["chapter_index"] == 2
    assert second.calls == [["仓库"]]
    with sqlite3.connect(database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM rag_vectors").fetchone()[0] == 3


def test_incremental_content_edit_embeds_only_changed_text():
    a = Embedder()
    original = build(a)
    b = Embedder()
    changed = build(b, NOVEL.replace("铜钥匙藏在茶罐。", "铜钥匙放在仓库。"))
    assert b.calls == [["铜钥匙放在仓库。"]]
    assert original.storage_info["index_id"] != changed.storage_info["index_id"]
    assert changed.storage_info["embedded_documents"] == 1
    assert len(list_indexes()) == 2


@pytest.mark.parametrize(
    "change", ["model", "revision", "provider", "account", "namespace", "chunking"]
)
def test_index_identity_isolates_model_source_and_chunk_configuration(monkeypatch, change):
    original = build()
    client = Embedder()
    if change == "model":
        client.embed_model = "another-model"
    elif change == "provider":
        client.base_url = "https://other.test"
    elif change == "account":
        client.api_key = "another-account"
    elif change == "revision":
        monkeypatch.setenv("RAG_EMBEDDING_REVISION", "2")
    elif change == "namespace":
        monkeypatch.setenv("RAG_NAMESPACE", "another-project")
    else:
        monkeypatch.setenv("RAG_CHUNK_CHARS", "4")
    changed = build(client)
    assert changed.storage_info["index_id"] != original.storage_info["index_id"]
    assert changed.storage_info["embedded_documents"] > 0
    # Neither provider credentials nor account hashes are exposed in the management list.
    serialized = json.dumps(list_indexes())
    assert client.api_key not in serialized and client.base_url not in serialized


def test_global_facts_and_before_chapter_are_filtered_before_semantic_ranking():
    chapters = parse_chapters(NOVEL)
    client = Embedder()
    kb = build_story_knowledge(
        chapters,
        extract_global_story_state(chapters),
        mode="ai",
        llm_client=client,
        retrieval="hybrid",
    )
    hits = kb.search("林夏雨茶罐", before_chapter=2)
    assert hits and all(h["chapter_index"] < 2 and h["kind"] in ("chunk", "event") for h in hits)
    before = len(client.calls)
    assert kb.search("雨", before_chapter=1) == []
    assert len(client.calls) == before  # Empty eligible set does not query remote embedding.


def test_other_novel_never_appears_in_search():
    first = build(novel="第一章\n秘密铜钥匙在茶罐里。")
    second = build(novel="第一章\n雨夜的仓库只有一把伞。")
    assert first.storage_info["source_hash"] != second.storage_info["source_hash"]
    assert not second.search("铜钥匙茶罐")


def test_zero_vectors_are_not_returned_as_nan_matches():
    kb = build(novel="第一章\n无关文字。\n第二章\n钥匙在茶罐。")
    hits = kb.search("钥匙")
    assert len(hits) == 1 and hits[0]["chapter_index"] == 2
    assert kb.search("没有语义信号") == []


@pytest.mark.parametrize(
    "vectors",
    [[[1.0, 2.0]], [[1.0, 2.0], [3.0]], [[float("nan")], [1.0]], [[True], [1.0]], [[], []]],
)
def test_malformed_vectors_never_publish_partial_index(vectors):
    class Malformed(Embedder):
        def embed(self, texts):
            return vectors

    kb = build(Malformed(), novel="第一章\n钥匙。\n第二章\n雨。")
    assert kb.retriever_kind == "lexical" and kb.warnings
    assert list_indexes() == []
    with sqlite3.connect(database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rag_vectors").fetchone()[0] == 0


def test_query_dimension_drift_fails_and_hybrid_falls_back():
    model = Embedder()
    kb = build(model, retrieval="hybrid")
    model.dimension = 5
    hits = kb.search("铜钥匙茶罐")
    assert hits and all(h["retrieval_degraded"] for h in hits)
    assert hits[0]["chapter_index"] == 1
    assert "回退" in kb.stats()["query_warning"]


def test_model_dimension_change_requires_new_revision(monkeypatch):
    build()
    client = Embedder(dimension=5)
    kb = build(client, novel="一段新的钥匙故事。")
    assert kb.retriever_kind == "lexical" and "维度已变化" in kb.warnings[0]
    monkeypatch.setenv("RAG_EMBEDDING_REVISION", "new-dim")
    assert build(client, novel="一段新的钥匙故事。").storage_info["dimension"] == 5


def test_failed_embedding_and_unwritable_database_degrade_honestly(monkeypatch, tmp_path):
    failed = build(Embedder(fail=True))
    assert failed.retriever_kind == "lexical" and not failed.storage_info["persistent"]
    path = tmp_path / "not-folder"
    path.write_text("file")
    monkeypatch.setenv("STORY2SCRIPT_RAG_DB", str(path / "vectors.db"))
    model = Embedder()
    kb = build(model)
    assert model.calls == [] and kb.retriever_kind == "lexical"
    assert kb.warnings and not kb.storage_info["persistent"]


def test_concurrent_builders_share_one_atomic_index():
    model = Embedder()
    with ThreadPoolExecutor(max_workers=4) as pool:
        indexes = list(pool.map(lambda _: build(model), range(4)))
    assert all(i.storage_info.get("persistent") for i in indexes), [i.warnings for i in indexes]
    assert len(model.calls) == 1
    assert len({i.storage_info["index_id"] for i in indexes}) == 1
    assert sum(i.storage_info["reused"] for i in indexes) == 3


def test_delete_preserves_shared_vectors_and_does_not_cross_namespace(monkeypatch):
    original = build()
    changed = build(novel=NOVEL.replace("铜钥匙藏在茶罐。", "铜钥匙放在雨里。"))
    delete_index(original.storage_info["index_id"])
    assert len(list_indexes()) == 1
    assert changed.search("仓库")[0]["chapter_index"] == 2
    with sqlite3.connect(database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rag_vectors").fetchone()[0] == 3
    monkeypatch.setenv("RAG_NAMESPACE", "different")
    assert list_indexes() == []
    with pytest.raises(KeyError):
        delete_index(changed.storage_info["index_id"])


def test_corrupt_document_metadata_refuses_false_reuse():
    kb = build()
    with sqlite3.connect(database_path()) as conn:
        conn.execute("UPDATE rag_documents SET text='篡改内容' WHERE ordinal=0")
    replacement = build()
    assert replacement.retriever_kind == "lexical" and "不完整" in replacement.warnings[0]
    assert kb.storage_info["index_id"] == list_indexes()[0]["index_id"]


def test_interrupted_write_is_rolled_back():
    kb = build()
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "CREATE TRIGGER reject_documents BEFORE INSERT ON rag_documents BEGIN SELECT RAISE(ABORT,'failure'); END"
        )
    changed = build(novel="钥匙现在在雨里的仓库。")
    assert changed.retriever_kind == "lexical"
    assert [r["index_id"] for r in list_indexes()] == [kb.storage_info["index_id"]]
    with sqlite3.connect(database_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rag_vectors").fetchone()[0] == 3


def test_memory_opt_out_does_not_create_database(monkeypatch):
    monkeypatch.setenv("RAG_STORAGE", "memory")
    kb = build()
    assert kb.retriever_kind == "embedding" and not kb.storage_info["persistent"]
    assert not database_path().exists()


def test_index_survives_an_actual_fresh_process():
    first = build()
    code = """import json
from story2script.parser import parse_chapters
from story2script.rag import build_story_knowledge
class ExistingOnly:
    base_url='https://embedding.test/v1'
    api_key='test-private-key'
    embed_model='test-vector-v1'
    def embed(self,texts):
        assert texts==['钥匙茶罐'],texts
        return [[1.,0.,1.,0.]]
novel='第一章 钥匙\\n铜钥匙藏在茶罐。\\n第二章 仓库\\n周宁走进仓库。\\n第三章 雨\\n林夏站在雨里。'
kb=build_story_knowledge(parse_chapters(novel),mode='ai',retrieval='embedding',llm_client=ExistingOnly())
print(json.dumps({'stats':kb.stats(),'hits':kb.search('钥匙茶罐')},ensure_ascii=True))
"""
    response = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert response.returncode == 0, response.stderr
    output = json.loads(response.stdout)
    assert output["stats"]["storage"]["reused"]
    assert output["stats"]["storage"]["index_id"] == first.storage_info["index_id"]
    assert output["hits"][0]["chapter_index"] == 1


def test_independent_processes_build_same_snapshot_once(tmp_path):
    code = """import os,json
from pathlib import Path
from story2script.parser import parse_chapters
from story2script.rag import build_story_knowledge
class Model:
    base_url='https://parallel.test/v1'
    api_key='fixture'
    embed_model='parallel-test'
    def embed(self,texts):
        with Path(os.environ['VECTOR_TEST_LOG']).open('a') as log: log.write('embed\\n')
        return [[1.,0.,0.] for t in texts]
kb=build_story_knowledge(parse_chapters('第一章\\n一段完整正文。'),mode='ai',retrieval='embedding',llm_client=Model())
print(json.dumps(kb.stats(),ensure_ascii=True))
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "VECTOR_TEST_LOG": str(tmp_path / "calls.log"),
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outputs = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            outputs.append(json.loads(stdout))
        assert all(row["storage"]["persistent"] for row in outputs), outputs
        assert len({row["storage"]["index_id"] for row in outputs}) == 1
        assert (tmp_path / "calls.log").read_text().splitlines() == ["embed"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_management_api_build_list_delete_and_missing_model(monkeypatch):
    import story2script.rag as module

    monkeypatch.setattr(module, "LLMClient", lambda **kwargs: Embedder())
    client = TestClient(app)
    built = client.post("/api/rag/indexes", json={"novel_text": NOVEL})
    assert built.status_code == 200, built.text
    index_id = built.json()["storage"]["index_id"]
    assert client.get("/api/rag/indexes").json()["indexes"][0]["index_id"] == index_id
    assert client.delete(f"/api/rag/indexes/{index_id}").status_code == 200
    assert client.delete(f"/api/rag/indexes/{index_id}").status_code == 404
    assert client.delete("/api/rag/indexes/invalid").status_code == 422

    class NoModel(Embedder):
        embed_model = ""

    monkeypatch.setattr(module, "LLMClient", lambda **kwargs: NoModel())
    assert client.post("/api/rag/indexes", json={"novel_text": NOVEL}).status_code == 422


def test_management_api_reports_storage_failure_and_empty_scope_without_model(
    monkeypatch, tmp_path
):
    import story2script.rag as module

    model = Embedder()
    monkeypatch.setattr(module, "LLMClient", lambda **kwargs: model)
    client = TestClient(app)
    built = client.post("/api/rag/indexes", json={"novel_text": NOVEL})
    assert built.status_code == 200
    before = len(model.calls)
    result = client.post(
        "/api/rag/query",
        json={
            "novel_text": NOVEL,
            "query": "钥匙",
            "mode": "ai",
            "before_chapter": 1,
            "retrieval": "embedding",
        },
    )
    assert result.status_code == 200 and result.json()["hits"] == []
    # /query includes global facts; one build may add those, but no query vector
    # should be requested for the empty reading range.
    assert all(call != ["钥匙"] for call in model.calls[before:])
    monkeypatch.setenv("STORY2SCRIPT_RAG_DB", str(tmp_path))
    assert client.get("/api/rag/indexes").status_code == 503


def test_large_corpus_filters_without_sql_variable_limit():
    docs = [
        KnowledgeDoc(str(i), "chunk", i + 1, f"第{i + 1}章", "钥匙" if i % 2 else "雨")
        for i in range(1100)
    ]
    model = Embedder()
    index = PersistentVectorIndex(model, docs, {"source_hash": digest("large"), "chunk_chars": 600})
    index.index([d.text for d in docs])
    scores = index.scores_eligible("钥匙", list(range(1000)))
    assert len(scores) == 1100 and all(v == 0 for v in scores[1000:])
    assert index.embedded_documents == 2
