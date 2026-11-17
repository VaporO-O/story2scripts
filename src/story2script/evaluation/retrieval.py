"""Independent retrieval benchmark: source evidence labels, ranking and cost."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..metrics import metrics
from ..parser import parse_chapters
from ..rag import build_story_knowledge
from ..story_qa import answer_story_question


class RetrievalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    query: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    before_chapter: int | None = Field(default=None, ge=1)


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    novel_text: str
    questions: list[RetrievalQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self):
        chapters = parse_chapters(self.novel_text)
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("问题 ID 不能重复。")
        for question in self.questions:
            if question.before_chapter and question.before_chapter > len(chapters) + 1:
                raise ValueError("前文章节范围超出小说。")
            allowed = chapters[:question.before_chapter - 1] if question.before_chapter else chapters
            for evidence in question.evidence:
                if not evidence or not any(evidence in chapter.content for chapter in allowed):
                    raise ValueError(f"{question.id} 的证据不在允许的原文范围内。")
        return self


class RetrievalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    split: Literal["dev", "holdout"]
    cases: list[RetrievalCase] = Field(min_length=1)


def score_ranking(hits: list[dict], relevant: set[str], top_k: int) -> dict:
    ids = list(dict.fromkeys(hit["doc_id"] for hit in hits))[:top_k]
    found = relevant.intersection(ids)
    rank = next((i for i, doc_id in enumerate(ids, 1) if doc_id in relevant), None)
    return {
        "recall": len(found) / len(relevant) if relevant else None,
        "reciprocal_rank": 1 / rank if rank else 0.0,
        "hit_at_1": bool(ids and ids[0] in relevant),
        "no_evidence": not relevant,
        # Empty retrieval is not a calibrated answer refusal.
        "empty_retrieval": not ids,
    }


def run_retrieval_eval(
    paths: list[str], variants: list[str], mode: str, top_k: int, output: str,
    *, answers: bool = False,
) -> dict:
    datasets = [RetrievalDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))
                for path in paths]
    seen = set()
    for dataset in datasets:
        for case in dataset.cases:
            if case.id in seen:
                raise ValueError(f"重复案例 ID：{case.id}")
            seen.add(case.id)
    rows, summaries = [], {}
    for variant in variants:
        if variant not in {"lexical", "embedding", "hybrid", "hybrid_rerank"}:
            raise ValueError(f"不支持的检索变体：{variant}")
        retrieval = "hybrid" if variant == "hybrid_rerank" else variant
        metrics.reset()
        started = time.perf_counter()
        variant_rows = []
        unavailable = ""
        for dataset in datasets:
            for case in dataset.cases:
                knowledge = build_story_knowledge(parse_chapters(case.novel_text), mode=mode,
                                                   retrieval=retrieval)
                if knowledge.retriever_kind != retrieval:
                    unavailable = f"请求 {retrieval}，实际降级为 {knowledge.retriever_kind}；不计作该变体的结果。"
                    break
                for question in case.questions:
                    relevant = {doc.doc_id for doc in knowledge.docs
                                if any(evidence in doc.text for evidence in question.evidence)}
                    if question.evidence and not relevant:
                        raise ValueError(f"{question.id} 的证据被切块截断，需调整分块或标注。")
                    tick = time.perf_counter()
                    hits = knowledge.search(question.query, top_k=top_k,
                                            before_chapter=question.before_chapter,
                                            kinds=("chunk",), rerank=variant == "hybrid_rerank")
                    latest_query = next((event for event in metrics.recent_events()
                                         if event.get("kind") == "rag_query"), {})
                    if (latest_query.get("extra", {}).get("degraded_to_lexical")
                        or latest_query.get("ok") is False):
                        unavailable = "查询阶段检索失败或降级；不计作该变体效果。"
                        break
                    if latest_query.get("extra", {}).get("rerank") == "failed_using_retrieval_order":
                        unavailable = "重排序失败；不把回退结果计作重排序效果。"
                        break
                    row = {"variant": variant, "split": dataset.split, "case_id": case.id,
                           "question_id": question.id, "query": question.query,
                           "relevant_ids": sorted(relevant), "retrieved_ids": [h["doc_id"] for h in hits],
                           "latency_ms": round((time.perf_counter() - tick) * 1000, 2),
                           **score_ranking(hits, relevant, top_k)}
                    if answers:
                        if mode != "ai":
                            raise ValueError("答案与拒答评测需要 --mode ai；本地模式只返回原文摘录。")
                        answer = answer_story_question(case.novel_text, question.query, mode=mode,
                            retrieval=retrieval, top_k=top_k, before_chapter=question.before_chapter,
                            rerank=variant == "hybrid_rerank")
                        cited = {citation["doc_id"] for citation in answer["citations"]}
                        row.update(abstained=answer["abstained"],
                            refusal_correct=answer["abstained"] == (not relevant),
                            evidence_citation_precision=(len(cited & relevant) / len(cited)
                                                         if cited else None),
                            answer=answer["answer"])
                    variant_rows.append(row)
                if unavailable:
                    break
            if unavailable:
                break
        if unavailable:
            summaries[variant] = {"status": "unavailable", "reason": unavailable}
            continue
        rows.extend(variant_rows)
        by_split = {}
        for split in sorted({row["split"] for row in variant_rows}):
            subset = [row for row in variant_rows if row["split"] == split]
            positives = [row for row in subset if not row["no_evidence"]]
            negatives = [row for row in subset if row["no_evidence"]]
            def mean(key):
                return round(sum(row[key] for row in positives) / len(positives), 4) if positives else None
            by_split[split] = {
                "questions": len(subset), "answerable": len(positives), "unanswerable": len(negatives),
                f"recall_at_{top_k}": mean("recall"), "mrr": mean("reciprocal_rank"),
                "hit_at_1": mean("hit_at_1"),
                "empty_retrieval_on_unanswerable": (sum(r["empty_retrieval"] for r in negatives)
                                                     / len(negatives) if negatives else None),
            }
            if answers:
                by_split[split]["refusal_accuracy"] = sum(r["refusal_correct"] for r in subset) / len(subset)
        summaries[variant] = {"status": "completed", "splits": by_split,
                              "duration_ms": round((time.perf_counter() - started) * 1000),
                              "llm": metrics.summary()["llm_overall"]}
    report = {"version": "1", "datasets": paths, "mode": mode, "top_k": top_k,
              "answers_evaluated": answers, "summary": summaries, "questions": rows}
    prefix = Path(output)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# RAG 检索评测", "", f"模式：{mode}；top-k：{top_k}；答案评测：{answers}",
             "按原文证据计算排名；无答案题不计入 Recall/MRR。空检索率不等于答案拒答正确率。",
             "引用命中只验证证据位置，答案的语义正确性仍需人工核验。", "",
             "| 方案 | 集合 | 问题数 | 可回答 | Recall@k | MRR | Hit@1 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for variant, summary in summaries.items():
        if summary["status"] != "completed":
            lines.append(f"\n{variant} 未运行：{summary['reason']}\n")
            continue
        for split, result in summary["splits"].items():
            lines.append(f"| {variant} | {split} | {result['questions']} | {result['answerable']} | "
                         f"{result[f'recall_at_{top_k}']} | {result['mrr']} | {result['hit_at_1']} |")
    prefix.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
