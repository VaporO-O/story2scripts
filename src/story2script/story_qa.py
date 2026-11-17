"""Evidence-grounded questions about a novel, with verifiable citations."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from .llm_client import LLMClient, loads_json_object
from .parser import parse_chapters
from .rag import build_story_knowledge
from .security import DATA_FENCE_NOTICE, screen_chat_message


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    quote: str = Field(min_length=1)


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    abstained: bool
    citations: list[EvidenceCitation] = Field(default_factory=list)


def answer_story_question(
    novel_text: str,
    query: str,
    *,
    mode: str = "demo",
    retrieval: str = "auto",
    top_k: int = 5,
    before_chapter: int | None = None,
    rerank: bool = False,
    client=None,
) -> dict:
    screen_chat_message(query)
    chapters = parse_chapters(novel_text)
    if before_chapter is not None and not 1 <= before_chapter <= len(chapters) + 1:
        raise ValueError("前文范围必须在第 1 章到全文结束之间。")
    llm = LLMClient(client=client, usage_label="AI story QA") if mode == "ai" else None
    knowledge = build_story_knowledge(chapters, mode=mode, llm_client=llm, retrieval=retrieval)
    hits = knowledge.search(
        query, top_k=top_k, before_chapter=before_chapter, kinds=("chunk",), rerank=rerank
    )
    response = {
        "retriever": knowledge.retriever_kind,
        "hits": hits,
        "answer": "在指定原文范围内没有找到可用依据。",
        "abstained": True,
        "citations": [],
        "answer_kind": "generated" if llm else "excerpts",
        "warnings": list(knowledge.warnings),
        "stats": knowledge.stats(),
    }
    if knowledge.query_warning:
        response["warnings"].append(knowledge.query_warning)
    if not hits:
        return response
    if any(hit["rerank_status"] == "failed_using_retrieval_order" for hit in hits):
        response["warnings"].append("重排序失败，本次使用初始检索顺序。")
    if llm is None:
        response.update(
            answer="以下是检索到的相关原文，请结合上下文核对。",
            abstained=False,
            citations=[
                {"doc_id": hit["doc_id"], "chapter": hit["chapter"], "quote": hit["snippet"]}
                for hit in hits
            ],
        )
        return response
    prompt = (
        "根据给定小说原文回答问题，不得使用原文之外的知识或未来剧情。"
        "原文不支持答案时 abstained=true，说明依据不足，citations=[]。"
        "有依据时必须引用候选中的 doc_id 和其中逐字连续的 quote；"
        "每个事实判断都要有引用，不要仅凭名字相同推断因果。"
        '只返回 JSON：{"answer":"回答", "abstained":false,'
        '"citations":[{"doc_id":"ch1-chunk1","quote":"原文摘录"}]}。\n'
        + DATA_FENCE_NOTICE
        + "\n"
        + json.dumps({"question": query, "evidence": hits}, ensure_ascii=False)
    )
    answer = GroundedAnswer.model_validate(
        loads_json_object(llm.complete_json(prompt, prompt_id="rag.answer"))
    )
    if answer.abstained:
        response["answer"] = answer.answer or response["answer"]
        return response
    known = {hit["doc_id"]: hit for hit in hits}
    if (
        not answer.answer.strip()
        or not answer.citations
        or any(
            citation.doc_id not in known
            or not citation.quote.strip()
            or citation.quote not in known[citation.doc_id]["snippet"]
            for citation in answer.citations
        )
    ):
        response["answer"] = "模型回答未提供可核对的原文引用，请查看检索片段或调整问题。"
        response["warnings"].append("引用校验失败，未采纳模型答案。")
        return response
    response.update(
        answer=answer.answer,
        abstained=False,
        citations=[
            {**citation.model_dump(), "chapter": known[citation.doc_id]["chapter"]}
            for citation in answer.citations
        ],
    )
    return response
