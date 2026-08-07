"""离线评测运行器：同一输入比较固定管线、单 Agent 与多 Agent。"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from ..agent import AdaptationAgent, AdaptationTeam
from ..converter import get_converter
from ..metrics import metrics
from ..parser import parse_chapters
from ..rag import build_story_knowledge
from ..scene_review import review_scenes_report
from ..screenplay import Screenplay
from .dataset import load_datasets
from .models import (
    AttributionMetrics,
    BehaviorMetrics,
    CaseReport,
    EvalCase,
    EvalReport,
    ScoreCounts,
    VariantMetrics,
)
from .scoring import score_behavior, score_output, score_source

VARIANTS = ("fixed_pipeline", "single_agent", "multi_agent")


def _clone(screenplay: Screenplay) -> Screenplay:
    return Screenplay.model_validate(screenplay.model_dump(mode="json"))


def _usage() -> dict:
    overall = metrics.summary().get("llm_overall", {})
    return {
        "llm_calls": int(overall.get("calls", 0)),
        "prompt_tokens": int(overall.get("prompt_tokens", 0)),
        "completion_tokens": int(overall.get("completion_tokens", 0)),
        "total_tokens": int(overall.get("total_tokens", 0)),
    }


def _score_summary(summary: dict) -> tuple[float, int, int]:
    return (
        float(summary.get("avg_score", 0.0)),
        int(summary.get("pass_count", 0)),
        int(summary.get("fail_count", 0)),
    )


def _fixed_pipeline(
    screenplay: Screenplay, case: EvalCase, mode: str, threshold: float
) -> VariantMetrics:
    metrics.reset()
    started = time.perf_counter()
    report = review_scenes_report(screenplay, mode=mode, threshold=threshold)
    duration_ms = int((time.perf_counter() - started) * 1000)
    usage = _usage()
    avg_score, pass_count, fail_count = _score_summary(report.summary)
    output = score_output(screenplay, case.expected)
    return VariantMetrics(
        variant="fixed_pipeline",
        status="completed",
        goal_achieved=fail_count == 0 and avg_score >= threshold,
        duration_ms=duration_ms,
        schema_valid=output.schema_valid,
        initial_avg_score=avg_score,
        final_avg_score=avg_score,
        score_delta=0.0,
        pass_count=pass_count,
        fail_count=fail_count,
        llm_calls=usage["llm_calls"],
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        behavior=BehaviorMetrics(),
        output=output,
    )


def _single_agent(
    screenplay: Screenplay,
    case: EvalCase,
    mode: str,
    threshold: float,
    max_steps: int,
    knowledge,
) -> VariantMetrics:
    metrics.reset()
    started = time.perf_counter()
    outcome = AdaptationAgent(
        mode=mode, threshold=threshold, max_steps=max_steps
    ).run(screenplay, goal="让全部场景通过审校。", knowledge=knowledge)
    duration_ms = int((time.perf_counter() - started) * 1000)
    usage = _usage()
    result = outcome.result
    initial_score, _initial_pass, _initial_fail = _score_summary(result.initial_summary)
    final_score, pass_count, fail_count = _score_summary(result.final_summary)
    output = score_output(outcome.screenplay, case.expected)
    return VariantMetrics(
        variant="single_agent",
        status=result.status,
        goal_achieved=fail_count == 0 and final_score >= threshold,
        duration_ms=duration_ms,
        schema_valid=output.schema_valid,
        initial_avg_score=initial_score,
        final_avg_score=final_score,
        score_delta=round(final_score - initial_score, 4),
        pass_count=pass_count,
        fail_count=fail_count,
        steps_used=result.steps_used,
        llm_calls=usage["llm_calls"],
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        behavior=score_behavior(result.trace, result.status, result.message),
        output=output,
    )


def _multi_agent(
    screenplay: Screenplay,
    case: EvalCase,
    mode: str,
    threshold: float,
    max_steps: int,
    max_rounds: int,
    knowledge,
) -> VariantMetrics:
    metrics.reset()
    started = time.perf_counter()
    outcome = AdaptationTeam(
        mode=mode,
        threshold=threshold,
        max_rounds=max_rounds,
        max_steps_per_agent=max_steps,
    ).run(screenplay, goal="兼顾场景质量与跨章一致性。", knowledge=knowledge)
    duration_ms = int((time.perf_counter() - started) * 1000)
    usage = _usage()
    result = outcome.result
    initial_score, _initial_pass, _initial_fail = _score_summary(result.initial_summary)
    final_score, pass_count, fail_count = _score_summary(result.final_summary)
    output = score_output(outcome.screenplay, case.expected)
    return VariantMetrics(
        variant="multi_agent",
        status=result.status,
        goal_achieved=fail_count == 0 and final_score >= threshold,
        duration_ms=duration_ms,
        schema_valid=output.schema_valid,
        initial_avg_score=initial_score,
        final_avg_score=final_score,
        score_delta=round(final_score - initial_score, 4),
        pass_count=pass_count,
        fail_count=fail_count,
        steps_used=len(result.trace),
        rounds_used=result.rounds_used,
        llm_calls=usage["llm_calls"],
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        behavior=score_behavior(result.trace, result.status, result.message),
        output=output,
    )


def _evaluate_case(
    case: EvalCase,
    split: str,
    mode: str,
    variants: tuple[str, ...],
    threshold: float,
    max_steps: int,
    max_rounds: int,
) -> CaseReport:
    chapters = parse_chapters(case.novel_text)
    if [chapter.title for chapter in chapters] != case.expected.chapter_titles:
        raise ValueError(f"评测样本 {case.id} 的章节标题与标注不一致。")

    metrics.reset()
    conversion_started = time.perf_counter()
    converter = get_converter(mode)
    screenplay = converter.convert(
        chapters,
        title=case.title,
        genre=case.genre,
        adaptation_type=case.adaptation_type,
    )
    knowledge = build_story_knowledge(
        chapters, screenplay.global_state, mode=mode
    )
    conversion_duration_ms = int((time.perf_counter() - conversion_started) * 1000)
    conversion_usage = _usage()

    source = score_source(chapters, screenplay, case.expected)
    results: dict[str, VariantMetrics] = {}
    if "fixed_pipeline" in variants:
        results["fixed_pipeline"] = _fixed_pipeline(
            _clone(screenplay), case, mode, threshold
        )
    if "single_agent" in variants:
        results["single_agent"] = _single_agent(
            _clone(screenplay), case, mode, threshold, max_steps, knowledge
        )
    if "multi_agent" in variants:
        results["multi_agent"] = _multi_agent(
            _clone(screenplay),
            case,
            mode,
            threshold,
            max_steps,
            max_rounds,
            knowledge,
        )
    return CaseReport(
        case_id=case.id,
        title=case.title,
        split=split,
        conversion_duration_ms=conversion_duration_ms,
        conversion_prompt_tokens=conversion_usage["prompt_tokens"],
        conversion_completion_tokens=conversion_usage["completion_tokens"],
        conversion_warnings=list(converter.last_run_warnings),
        source=source,
        variants=results,
    )


def _aggregate_counts(items: list[ScoreCounts]) -> ScoreCounts:
    expected = sum(item.expected for item in items)
    predicted = sum(item.predicted for item in items)
    correct = sum(item.correct for item in items)
    precision = round(correct / predicted, 4) if predicted else (1.0 if not expected else 0.0)
    recall = round(correct / expected, 4) if expected else 1.0
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    return ScoreCounts(
        expected=expected,
        predicted=predicted,
        correct=correct,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _mean(values: list[float | int]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _aggregate_attribution(items: list[AttributionMetrics]) -> dict:
    expected = sum(item.expected for item in items)
    matched = sum(item.matched for item in items)
    correct = sum(item.correct for item in items)
    return {
        "expected": expected,
        "matched": matched,
        "correct": correct,
        "accuracy": round(correct / expected, 4) if expected else 1.0,
    }


def _summarize(cases: list[CaseReport], variants: tuple[str, ...]) -> dict:
    boundaries = _aggregate_counts([case.source.scene_boundaries for case in cases])
    continuity = _aggregate_counts([case.source.continuity_probe for case in cases])
    summary: dict = {
        "case_count": len(cases),
        "source": {
            "boundary_precision": boundaries.precision,
            "boundary_recall": boundaries.recall,
            "boundary_f1": boundaries.f1,
            "continuity_precision": continuity.precision,
            "continuity_recall": continuity.recall,
            "continuity_f1": continuity.f1,
        },
        "variants": {},
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    for variant in variants:
        rows = [case.variants[variant] for case in cases if variant in case.variants]
        characters = _aggregate_counts([row.output.characters for row in rows])
        dialogue = _aggregate_attribution(
            [row.output.dialogue_attribution for row in rows]
        )
        action_count = sum(row.behavior.action_count for row in rows)
        invalid_actions = sum(row.behavior.invalid_action_count for row in rows)
        repeated_actions = sum(row.behavior.repeated_action_count for row in rows)
        passed_scenes = sum(row.pass_count for row in rows)
        reviewed_scenes = passed_scenes + sum(row.fail_count for row in rows)
        summary["variants"][variant] = {
            "run_count": len(rows),
            "workflow_completion_rate": _mean(
                [row.status == "completed" for row in rows]
            ),
            "goal_achieved_rate": _mean([row.goal_achieved for row in rows]),
            "schema_valid_rate": _mean([row.schema_valid for row in rows]),
            "chapter_title_accuracy": _mean(
                [row.output.chapter_title_accuracy for row in rows]
            ),
            "character_precision": characters.precision,
            "character_recall": characters.recall,
            "character_f1": characters.f1,
            "dialogue_accuracy": dialogue["accuracy"],
            "avg_initial_score": _mean([row.initial_avg_score for row in rows]),
            "avg_final_score": _mean([row.final_avg_score for row in rows]),
            "avg_score_delta": _mean([row.score_delta for row in rows]),
            "scene_pass_rate": (
                round(passed_scenes / reviewed_scenes, 4) if reviewed_scenes else 0.0
            ),
            "tool_legal_rate": (
                round((action_count - invalid_actions) / action_count, 4)
                if action_count
                else 1.0
            ),
            "repeated_action_rate": (
                round(repeated_actions / action_count, 4) if action_count else 0.0
            ),
            "circuit_breaker_rate": _mean(
                [row.behavior.circuit_breaker_triggered for row in rows]
            ),
            "avg_duration_ms": _mean([row.duration_ms for row in rows]),
            "total_llm_calls": sum(row.llm_calls for row in rows),
            "total_prompt_tokens": sum(row.prompt_tokens for row in rows),
            "total_completion_tokens": sum(row.completion_tokens for row in rows),
            "total_tokens": sum(row.total_tokens for row in rows),
            "dialogue_counts": dialogue,
        }
    single = summary["variants"].get("single_agent")
    team = summary["variants"].get("multi_agent")
    if single is not None and team is not None:
        single_duration = float(single["avg_duration_ms"])
        single_tokens = int(single["total_tokens"])
        summary["single_vs_multi"] = {
            "final_score_delta": round(
                float(team["avg_final_score"]) - float(single["avg_final_score"]), 4
            ),
            "goal_achieved_rate_delta": round(
                float(team["goal_achieved_rate"])
                - float(single["goal_achieved_rate"]),
                4,
            ),
            "latency_ratio": (
                round(float(team["avg_duration_ms"]) / single_duration, 4)
                if single_duration
                else None
            ),
            "token_ratio": (
                round(int(team["total_tokens"]) / single_tokens, 4)
                if single_tokens
                else None
            ),
        }
    return summary


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def evaluate_datasets(
    dataset_paths: list[str | Path],
    mode: str = "demo",
    variants: tuple[str, ...] = VARIANTS,
    threshold: float = 8.9,
    max_steps: int = 12,
    max_rounds: int = 6,
) -> EvalReport:
    if mode not in {"demo", "ai"}:
        raise ValueError(f"不支持的评测模式：{mode}")
    unknown_variants = set(variants) - set(VARIANTS)
    if unknown_variants:
        raise ValueError(f"不支持的评测变体：{', '.join(sorted(unknown_variants))}")
    if not variants:
        raise ValueError("至少需要一个评测变体。")

    datasets = load_datasets(dataset_paths)
    cases: list[CaseReport] = []
    for dataset in datasets:
        for case in dataset.cases:
            cases.append(
                _evaluate_case(
                    case,
                    dataset.split,
                    mode,
                    variants,
                    threshold,
                    max_steps,
                    max_rounds,
                )
            )

    return EvalReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=_git_commit(),
        dataset_versions=sorted({dataset.version for dataset in datasets}),
        splits=sorted({dataset.split for dataset in datasets}),
        mode=mode,
        model=os.getenv("AI_MODEL", "") if mode == "ai" else "demo-rules",
        threshold=threshold,
        max_steps=max_steps,
        max_rounds=max_rounds,
        cases=cases,
        summary=_summarize(cases, variants),
    )
