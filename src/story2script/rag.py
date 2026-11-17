"""RAG 前文知识库：把章节分块与全局事实建成可检索索引。

用途：
- AI 转换第 N 章片段时，只注入检索出的 top-k 相关前文（严格排除未来章节），
  替代随章节数线性膨胀的全量上下文；
- 改编 Agent 获得 ``search_story_context`` 工具；
- REST / MCP 暴露查询能力。

检索器沿用项目 demo/ai 双模式：词法检索（纯 Python 字符 bigram TF-IDF + 余弦，
零依赖、确定性、离线可用）与 Embedding 检索（OpenAI-compatible ``/embeddings``，
``AI_EMBED_MODEL`` 未配置或建库失败时自动降级词法，主链路永不因 RAG 中断）。
"""

from __future__ import annotations

import math
import os
import re
import time
import json
from collections import Counter
from dataclasses import dataclass, field

from .llm_client import LLMClient, loads_json_object
from .metrics import metrics
from .parser import Chapter
from .screenplay import GlobalStoryState
from .security import DATA_FENCE_NOTICE, redact_secrets
from .vector_store import PersistentVectorIndex, digest, normalized, storage_mode

RAG_TOP_K_ENV = "RAG_TOP_K"
RAG_CHUNK_CHARS_ENV = "RAG_CHUNK_CHARS"
DEFAULT_TOP_K = 3
DEFAULT_CHUNK_CHARS = 600
SNIPPET_CHARS = 200

_TOKEN_PATTERN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")
_SINGLE_STOPWORDS = set("的了是在把将与和而吗呢呀啊哪什么为有个这那") | {
    "the",
    "is",
    "where",
    "what",
    "why",
    "how",
    "when",
    "who",
    "a",
    "an",
    "of",
    "to",
    "and",
    "in",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数。") from exc


def rag_top_k() -> int:
    return _env_int(RAG_TOP_K_ENV, DEFAULT_TOP_K)


@dataclass(frozen=True)
class KnowledgeDoc:
    """索引中的一条文档：章节片段或全局事实（人物/地点/时间线）。"""

    doc_id: str
    kind: str  # chunk | character | location | event
    chapter_index: int  # 1-based；全局事实（人物/地点）为 0
    chapter_title: str
    text: str
    metadata: dict = field(default_factory=dict)


def _tokenize(text: str) -> list[str]:
    """单字 + 相邻 bigram：对中文专名（人名/地名）足够敏感，纯 Python 可实现。"""
    units = _TOKEN_PATTERN.findall(text.lower())
    tokens = [unit for unit in units if unit not in _SINGLE_STOPWORDS]
    tokens.extend(units[i] + units[i + 1] for i in range(len(units) - 1))
    return tokens


class LexicalRetriever:
    """字符 bigram TF-IDF + 余弦相似度，确定性、零外部依赖。"""

    kind = "lexical"

    def __init__(self) -> None:
        self._idf: dict[str, float] = {}
        self._doc_vectors: list[dict[str, float]] = []

    def index(self, texts: list[str]) -> None:
        doc_tokens = [Counter(_tokenize(text)) for text in texts]
        doc_count = len(texts)
        df: Counter[str] = Counter()
        for tokens in doc_tokens:
            df.update(tokens.keys())
        self._idf = {
            token: math.log((doc_count + 1) / (count + 1)) + 1.0 for token, count in df.items()
        }
        self._doc_vectors = [self._vectorize(tokens) for tokens in doc_tokens]

    def _vectorize(self, tokens: Counter[str]) -> dict[str, float]:
        vector = {token: count * self._idf.get(token, 0.0) for token, count in tokens.items()}
        norm = math.sqrt(sum(value * value for value in vector.values()))
        if norm > 0:
            vector = {token: value / norm for token, value in vector.items()}
        return vector

    def scores(self, query: str) -> list[float]:
        query_vector = self._vectorize(Counter(_tokenize(query)))
        results = []
        for doc_vector in self._doc_vectors:
            if len(query_vector) > len(doc_vector):
                small, large = doc_vector, query_vector
            else:
                small, large = query_vector, doc_vector
            score = sum(value * large.get(token, 0.0) for token, value in small.items())
            results.append(score)
        return results


class EmbeddingRetriever:
    """语义检索：建库时批量 embed 文档，查询时 embed query，余弦打分。"""

    kind = "embedding"

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client
        self._doc_vectors: list[list[float]] = []

    def index(self, texts: list[str]) -> None:
        vectors = self._llm_client.embed(texts)
        if len(vectors) != len(texts):
            raise ValueError("Embedding 返回数量与文档数不一致。")
        self._doc_vectors = [normalized(vector) for vector in vectors]
        if len({len(v) for v in self._doc_vectors}) > 1:
            raise ValueError("Embedding 文档向量维度不一致。")

    def scores(self, query: str) -> list[float]:
        query_vector = normalized(self._llm_client.embed([query])[0])
        if any(len(vector) != len(query_vector) for vector in self._doc_vectors):
            raise ValueError("Embedding 文档与查询向量维度不一致。")
        return [
            sum(a * b for a, b in zip(query_vector, doc_vector)) for doc_vector in self._doc_vectors
        ]


class HybridRetriever:
    """Keep lexical and semantic ranks separate until chapter filtering."""

    kind = "hybrid"

    def __init__(self, lexical: LexicalRetriever, semantic: EmbeddingRetriever) -> None:
        self.lexical = lexical
        self.semantic = semantic

    def components(self, query: str, eligible=None) -> tuple[list[list[float]], bool]:
        lexical = self.lexical.scores(query)
        try:
            semantic = (
                self.semantic.scores_eligible(query, eligible)
                if hasattr(self.semantic, "scores_eligible") and eligible is not None
                else self.semantic.scores(query)
            )
            return [lexical, semantic], False
        except ValueError:
            return [lexical], True


def _snippet_for_query(text: str, query: str, limit: int = SNIPPET_CHARS) -> tuple[str, int, int]:
    """Select an evidence window instead of always truncating the first 200 chars."""
    if len(text) <= limit:
        return text, 0, len(text)
    tokens = set(_tokenize(query))
    starts = set(range(0, len(text) - limit + 1, max(1, limit // 2)))
    starts.add(len(text) - limit)
    start = max(
        sorted(starts),
        key=lambda i: sum(len(token) ** 2 for token in tokens if token in text[i : i + limit]),
    )
    return text[start : start + limit], start, start + limit


def _chapter_chunk_docs(chapters: list[Chapter], chunk_chars: int) -> list[KnowledgeDoc]:
    docs: list[KnowledgeDoc] = []
    for chapter_no, chapter in enumerate(chapters, start=1):
        paragraphs = [part.strip() for part in chapter.content.splitlines() if part.strip()]
        pieces: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n{paragraph}" if buffer else paragraph
            if buffer and len(candidate) > chunk_chars:
                pieces.append(buffer)
                buffer = paragraph
            else:
                buffer = candidate
            while len(buffer) > chunk_chars:
                pieces.append(buffer[:chunk_chars])
                buffer = buffer[chunk_chars:]
        if buffer:
            pieces.append(buffer)
        if not pieces and chapter.content.strip():
            pieces = [chapter.content.strip()]
        for piece_no, piece in enumerate(pieces, start=1):
            docs.append(
                KnowledgeDoc(
                    doc_id=f"ch{chapter_no}-chunk{piece_no}",
                    kind="chunk",
                    chapter_index=chapter_no,
                    chapter_title=chapter.title,
                    text=piece,
                )
            )
    return docs


def _fact_docs(global_state: GlobalStoryState) -> list[KnowledgeDoc]:
    docs: list[KnowledgeDoc] = []
    for character in global_state.characters:
        traits = "、".join(character.traits) or "暂无"
        chapters_text = "、".join(character.appearance_chapters)
        docs.append(
            KnowledgeDoc(
                doc_id=f"fact-{character.id}",
                kind="character",
                chapter_index=0,
                chapter_title="",
                text=(
                    f"人物 {character.name}：特质 {traits}；目标 {character.goal}；"
                    f"弧光 {character.arc}；出场章节 {chapters_text}。{character.consistency_note}"
                ),
                metadata={"id": character.id, "name": character.name},
            )
        )
    for location in global_state.locations:
        chapters_text = "、".join(location.appearance_chapters)
        docs.append(
            KnowledgeDoc(
                doc_id=f"fact-{location.id}",
                kind="location",
                chapter_index=0,
                chapter_title="",
                text=(
                    f"地点 {location.name}：{location.description}"
                    f"（首次出现于 {location.first_appearance}；出场章节 {chapters_text}）"
                ),
                metadata={"id": location.id, "name": location.name},
            )
        )
    for event in global_state.timeline:
        marker = f"（{event.time_marker}）" if event.time_marker else ""
        docs.append(
            KnowledgeDoc(
                doc_id=f"fact-{event.id}",
                kind="event",
                chapter_index=event.order,
                chapter_title=event.chapter,
                text=f"时间线 {event.chapter}{marker}：{event.summary}",
                metadata={"id": event.id, "order": event.order},
            )
        )
    return docs


class StoryKnowledgeBase:
    """故事知识库：文档 + 可插拔检索器，查询带防未来泄漏过滤。"""

    def __init__(self, docs: list[KnowledgeDoc], retriever, rerank_client=None) -> None:
        self.docs = docs
        self._retriever = retriever
        self._rerank_client = rerank_client
        self.storage_info = {"backend": "memory", "persistent": False}
        self.warnings = []
        self.query_warning = ""

    @property
    def retriever_kind(self) -> str:
        return self._retriever.kind

    def stats(self) -> dict:
        by_kind: Counter[str] = Counter(doc.kind for doc in self.docs)
        return {
            "doc_count": len(self.docs),
            "by_kind": dict(sorted(by_kind.items())),
            "retriever": self.retriever_kind,
            "storage": self.storage_info,
            "warnings": self.warnings,
            "query_warning": self.query_warning,
        }

    def search(
        self,
        query: str,
        top_k: int | None = None,
        before_chapter: int | None = None,
        kinds: tuple[str, ...] | list[str] | None = None,
        rerank: bool = False,
    ) -> list[dict]:
        """检索相关文档。

        ``before_chapter=N`` 时排除第 N 章及之后的文档，也排除概括全文的人物/地点
        事实（其内容可能包含未来事件）。检索失败（如 embedding
        服务异常）不抛错，返回空列表，保证调用方主流程不中断。
        """
        resolved_top_k = rag_top_k() if top_k is None else max(1, int(top_k))
        allowed_kinds = set(kinds) if kinds else None
        eligible = [
            i
            for i, d in enumerate(self.docs)
            if (allowed_kinds is None or d.kind in allowed_kinds)
            and (
                before_chapter is None
                or (d.kind not in ("character", "location") and d.chapter_index < before_chapter)
            )
        ]
        started = time.perf_counter()
        self.query_warning = ""
        try:
            if isinstance(self._retriever, HybridRetriever):
                components, degraded = self._retriever.components(query, eligible)
            elif isinstance(self._retriever, PersistentVectorIndex):
                components, degraded = [self._retriever.scores_eligible(query, eligible)], False
            else:
                components, degraded = [self._retriever.scores(query)], False
        except ValueError as exc:
            self.query_warning = "语义查询失败，本次未返回语义结果：" + redact_secrets(str(exc))
            metrics.record_task(
                "rag_query",
                mode=self.retriever_kind,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error=str(exc),
                extra={"top_k": resolved_top_k},
            )
            return []

        if degraded:
            self.query_warning = "语义查询暂不可用，本次结果已回退到词法检索。"

        scores = [0.0] * len(self.docs)
        if len(components) == 1:
            scores = components[0]
        else:
            # RRF is computed AFTER filtering; excluded future chapters cannot
            # push eligible evidence out of either candidate ranking.
            for values in components:
                order = sorted(
                    (i for i in eligible if values[i] > 0),
                    key=lambda i: (-values[i], self.docs[i].doc_id),
                )
                rank, previous = 0, None
                for position, index in enumerate(order, 1):
                    if previous is None or values[index] != previous:
                        rank = position
                    previous = values[index]
                    scores[index] += 1 / (60 + rank)
        candidates = [(scores[i], self.docs[i]) for i in eligible if scores[i] > 0]
        candidates.sort(key=lambda item: (-item[0], item[1].doc_id))
        rerank_status = "disabled"
        if rerank and candidates:
            if self._rerank_client is None:
                raise ValueError("语义重排序需要 AI 模式与已配置的生成模型。")
            pool = candidates[: min(20, max(10, resolved_top_k * 3))]
            try:
                payload = loads_json_object(
                    self._rerank_client.complete_json(
                        '按问题相关性排序候选原文，只返回 JSON：{"doc_ids":["候选ID"]}。'
                        "只返回相关文档，不能新增ID。\n"
                        + DATA_FENCE_NOTICE
                        + "\n"
                        + json.dumps(
                            {
                                "query": query,
                                "candidates": [
                                    {"doc_id": doc.doc_id, "text": doc.text} for _, doc in pool
                                ],
                            },
                            ensure_ascii=False,
                        ),
                        prompt_id="rag.rerank",
                    )
                )
                ids = payload.get("doc_ids") if isinstance(payload, dict) else None
                known = {doc.doc_id: (score, doc) for score, doc in pool}
                if (
                    not isinstance(ids, list)
                    or any(not isinstance(i, str) or i not in known for i in ids)
                    or len(ids) != len(set(ids))
                ):
                    raise ValueError("重排序返回了无效文档引用。")
                candidates = [known[i] for i in ids]
                rerank_status = "applied"
            except ValueError:
                rerank_status = "failed_using_retrieval_order"
        hits = []
        for score, doc in candidates[:resolved_top_k]:
            snippet, start, end = _snippet_for_query(doc.text, query)
            hits.append(
                {
                    "doc_id": doc.doc_id,
                    "kind": doc.kind,
                    "chapter": doc.chapter_title,
                    "chapter_index": doc.chapter_index,
                    "score": round(score, 4),
                    "snippet": snippet,
                    "snippet_start": start,
                    "snippet_end": end,
                    "rerank_status": rerank_status,
                    "retrieval_degraded": degraded,
                }
            )
        metrics.record_task(
            "rag_query",
            mode=self.retriever_kind,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=True,
            extra={
                "top_k": resolved_top_k,
                "hits": len(hits),
                "degraded_to_lexical": degraded,
                "rerank": rerank_status,
            },
        )
        return hits


def build_story_knowledge(
    chapters: list[Chapter],
    global_state: GlobalStoryState | None = None,
    mode: str = "demo",
    llm_client: LLMClient | None = None,
    client=None,
    retrieval: str = "auto",
) -> StoryKnowledgeBase:
    """建库入口。mode="ai" 且配置了 AI_EMBED_MODEL 时用语义检索，否则词法。"""
    started = time.perf_counter()
    chunk_chars = _env_int(RAG_CHUNK_CHARS_ENV, DEFAULT_CHUNK_CHARS)
    store_mode = storage_mode()
    docs = _chapter_chunk_docs(chapters, chunk_chars)
    if global_state is not None:
        docs.extend(_fact_docs(global_state))
    texts = [doc.text for doc in docs]
    if retrieval == "auto":
        retrieval = os.getenv("RAG_RETRIEVAL", "hybrid" if mode == "ai" else "lexical")
    if retrieval not in {"lexical", "embedding", "hybrid"}:
        raise ValueError("RAG_RETRIEVAL 必须是 lexical、embedding 或 hybrid。")
    lexical = LexicalRetriever()
    lexical.index(texts)
    retriever = None
    resolved_client = None
    storage_info = {"backend": "memory", "persistent": False, "requested": store_mode}
    warnings = []
    if mode == "ai":
        resolved_client = llm_client or LLMClient(client=client, usage_label="AI embeddings")
        if resolved_client.embed_model and retrieval != "lexical":
            candidate = (
                PersistentVectorIndex(
                    resolved_client,
                    docs,
                    {
                        "source_hash": digest(
                            [{"title": c.title, "content": c.content} for c in chapters]
                        ),
                        "chunk_chars": chunk_chars,
                        "chunker_version": "chapter-lines-v1",
                    },
                )
                if store_mode == "sqlite"
                else EmbeddingRetriever(resolved_client)
            )
            try:
                candidate.index(texts)
                retriever = (
                    HybridRetriever(lexical, candidate) if retrieval == "hybrid" else candidate
                )
                if isinstance(candidate, PersistentVectorIndex):
                    storage_info = candidate.stats()
            except ValueError as exc:
                # embedding 建库失败（服务不可用/配置错误）降级词法，不阻断主链路。
                retriever = None
                warnings.append(f"语义索引不可用，已降级词法检索：{redact_secrets(str(exc))}")
        elif retrieval != "lexical":
            warnings.append("未配置 Embedding 模型，使用词法检索；本次未建立持久化向量索引。")
    if retriever is None:
        retriever = lexical

    knowledge = StoryKnowledgeBase(docs, retriever, rerank_client=resolved_client)
    knowledge.storage_info = storage_info
    knowledge.warnings = warnings
    metrics.record_task(
        "rag_index",
        mode=mode,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=True,
        extra={"docs": len(docs), "retriever": knowledge.retriever_kind, "storage": storage_info},
    )
    return knowledge
