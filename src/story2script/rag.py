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
from collections import Counter
from dataclasses import dataclass, field

from .llm_client import LLMClient
from .metrics import metrics
from .parser import Chapter
from .screenplay import GlobalStoryState

RAG_TOP_K_ENV = "RAG_TOP_K"
RAG_CHUNK_CHARS_ENV = "RAG_CHUNK_CHARS"
DEFAULT_TOP_K = 3
DEFAULT_CHUNK_CHARS = 600
SNIPPET_CHARS = 200

_TOKEN_PATTERN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


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
    tokens = list(units)
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
            token: math.log((doc_count + 1) / (count + 1)) + 1.0
            for token, count in df.items()
        }
        self._doc_vectors = [self._vectorize(tokens) for tokens in doc_tokens]

    def _vectorize(self, tokens: Counter[str]) -> dict[str, float]:
        vector = {
            token: count * self._idf.get(token, 0.0) for token, count in tokens.items()
        }
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
        self._doc_vectors = [_normalized(vector) for vector in self._llm_client.embed(texts)]

    def scores(self, query: str) -> list[float]:
        query_vector = _normalized(self._llm_client.embed([query])[0])
        return [
            sum(a * b for a, b in zip(query_vector, doc_vector))
            for doc_vector in self._doc_vectors
        ]


def _normalized(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


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

    def __init__(self, docs: list[KnowledgeDoc], retriever) -> None:
        self.docs = docs
        self._retriever = retriever

    @property
    def retriever_kind(self) -> str:
        return self._retriever.kind

    def stats(self) -> dict:
        by_kind: Counter[str] = Counter(doc.kind for doc in self.docs)
        return {
            "doc_count": len(self.docs),
            "by_kind": dict(sorted(by_kind.items())),
            "retriever": self.retriever_kind,
        }

    def search(
        self,
        query: str,
        top_k: int | None = None,
        before_chapter: int | None = None,
        kinds: tuple[str, ...] | list[str] | None = None,
    ) -> list[dict]:
        """检索相关文档。

        ``before_chapter=N`` 时排除第 N 章及之后的 chunk/event 文档（防未来剧情
        泄漏）；人物/地点是全局事实，不受该过滤影响。检索失败（如 embedding
        服务异常）不抛错，返回空列表，保证调用方主流程不中断。
        """
        resolved_top_k = rag_top_k() if top_k is None else max(1, int(top_k))
        started = time.perf_counter()
        try:
            scores = self._retriever.scores(query)
        except ValueError as exc:
            metrics.record_task(
                "rag_query",
                mode=self.retriever_kind,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error=str(exc),
                extra={"top_k": resolved_top_k},
            )
            return []

        allowed_kinds = set(kinds) if kinds else None
        candidates: list[tuple[float, KnowledgeDoc]] = []
        for score, doc in zip(scores, self.docs):
            if score <= 0:
                continue
            if allowed_kinds is not None and doc.kind not in allowed_kinds:
                continue
            if (
                before_chapter is not None
                and doc.kind in ("chunk", "event")
                and doc.chapter_index >= before_chapter
            ):
                continue
            candidates.append((score, doc))
        candidates.sort(key=lambda item: (-item[0], item[1].doc_id))

        hits = [
            {
                "doc_id": doc.doc_id,
                "kind": doc.kind,
                "chapter": doc.chapter_title,
                "score": round(score, 4),
                "snippet": doc.text[:SNIPPET_CHARS],
            }
            for score, doc in candidates[:resolved_top_k]
        ]
        metrics.record_task(
            "rag_query",
            mode=self.retriever_kind,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=True,
            extra={"top_k": resolved_top_k, "hits": len(hits)},
        )
        return hits


def build_story_knowledge(
    chapters: list[Chapter],
    global_state: GlobalStoryState | None = None,
    mode: str = "demo",
    llm_client: LLMClient | None = None,
    client=None,
) -> StoryKnowledgeBase:
    """建库入口。mode="ai" 且配置了 AI_EMBED_MODEL 时用语义检索，否则词法。"""
    started = time.perf_counter()
    chunk_chars = _env_int(RAG_CHUNK_CHARS_ENV, DEFAULT_CHUNK_CHARS)
    docs = _chapter_chunk_docs(chapters, chunk_chars)
    if global_state is not None:
        docs.extend(_fact_docs(global_state))
    texts = [doc.text for doc in docs]

    retriever = None
    if mode == "ai":
        resolved_client = llm_client or LLMClient(client=client, usage_label="AI embeddings")
        if resolved_client.embed_model:
            candidate = EmbeddingRetriever(resolved_client)
            try:
                candidate.index(texts)
                retriever = candidate
            except ValueError:
                # embedding 建库失败（服务不可用/配置错误）降级词法，不阻断主链路。
                retriever = None
    if retriever is None:
        retriever = LexicalRetriever()
        retriever.index(texts)

    knowledge = StoryKnowledgeBase(docs, retriever)
    metrics.record_task(
        "rag_index",
        mode=mode,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=True,
        extra={"docs": len(docs), "retriever": knowledge.retriever_kind},
    )
    return knowledge
