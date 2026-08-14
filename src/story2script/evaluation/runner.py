"""离线评测运行器：同一输入比较固定管线、单 Agent 与多 Agent。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..agent import AdaptationAgent, AdaptationTeam
from ..converter import get_converter
from ..llm_cache import CACHE_DISABLE_ENV
from ..llm_client import LLMClient
from ..metrics import metrics
from ..parser import parse_chapters
from ..prompt_catalog import current_prompt_versions
from ..rag import build_story_knowledge
from ..scene_review import review_scenes_report
from ..screenplay import Screenplay
from ..security import redact_secrets
from .dataset import load_datasets
from .models import (
    AttributionMetrics,
    BehaviorMetrics,
    CaseReport,
    EvalCase,
    EvalFailure,
    EvalReport,
    ExecutionMetadata,
    ScoreCounts,
    TokenPricing,
    VariantMetrics,
)
from .scoring import EvaluationDataError, score_behavior, score_output, score_source
from .statistics import summarize_values

VARIANTS = ("fixed_pipeline", "single_agent", "multi_agent")
CHECKPOINT_VERSION = "2"


def _clone(screenplay: Screenplay) -> Screenplay:
    return Screenplay.model_validate(screenplay.model_dump(mode="json"))


def _usage() -> dict:
    overall = metrics.summary().get("llm_overall", {})
    return {
        "llm_calls": int(overall.get("calls", 0)),
        "prompt_tokens": int(overall.get("prompt_tokens", 0)),
        "completion_tokens": int(overall.get("completion_tokens", 0)),
        "total_tokens": int(overall.get("total_tokens", 0)),
        "cache_hits": int(overall.get("cache_hits", 0)),
    }


def _estimated_cost(usage: dict, pricing: TokenPricing | None) -> float | None:
    if pricing is None:
        return None
    cost = (
        usage["prompt_tokens"] * pricing.input_per_million
        + usage["completion_tokens"] * pricing.output_per_million
    ) / 1_000_000
    return round(cost, 8)


def _score_summary(summary: dict) -> tuple[float, int, int]:
    return (
        float(summary.get("avg_score", 0.0)),
        int(summary.get("pass_count", 0)),
        int(summary.get("fail_count", 0)),
    )


def _failed_variant(
    variant: str,
    screenplay: Screenplay,
    case: EvalCase,
    error: Exception,
    duration_ms: int,
    pricing: TokenPricing | None,
) -> VariantMetrics:
    usage = _usage()
    return VariantMetrics(
        variant=variant,
        status="failed",
        error=redact_secrets(str(error))[:500],
        goal_achieved=False,
        duration_ms=duration_ms,
        schema_valid=True,
        fail_count=len(screenplay.scenes),
        llm_calls=usage["llm_calls"],
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        cache_hits=usage["cache_hits"],
        estimated_cost=_estimated_cost(usage, pricing),
        behavior=BehaviorMetrics(),
        output=score_output(screenplay, case.expected),
        screenplay=screenplay,
    )


def _fixed_pipeline(
    screenplay: Screenplay,
    case: EvalCase,
    mode: str,
    threshold: float,
    pricing: TokenPricing | None,
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
        cache_hits=usage["cache_hits"],
        estimated_cost=_estimated_cost(usage, pricing),
        behavior=BehaviorMetrics(),
        output=output,
        screenplay=screenplay,
    )


def _single_agent(
    screenplay: Screenplay,
    case: EvalCase,
    mode: str,
    threshold: float,
    max_steps: int,
    knowledge,
    pricing: TokenPricing | None,
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
        cache_hits=usage["cache_hits"],
        estimated_cost=_estimated_cost(usage, pricing),
        behavior=score_behavior(result.trace, result.status, result.message),
        output=output,
        screenplay=outcome.screenplay,
    )


def _multi_agent(
    screenplay: Screenplay,
    case: EvalCase,
    mode: str,
    threshold: float,
    max_steps: int,
    max_rounds: int,
    knowledge,
    pricing: TokenPricing | None,
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
        cache_hits=usage["cache_hits"],
        estimated_cost=_estimated_cost(usage, pricing),
        behavior=score_behavior(result.trace, result.status, result.message),
        output=output,
        screenplay=outcome.screenplay,
    )


def _evaluate_case(
    case: EvalCase,
    split: str,
    mode: str,
    variants: tuple[str, ...],
    threshold: float,
    max_steps: int,
    max_rounds: int,
    sample_index: int,
    pricing: TokenPricing | None,
) -> CaseReport:
    chapters = parse_chapters(case.novel_text)
    if [chapter.title for chapter in chapters] != case.expected.chapter_titles:
        raise EvaluationDataError(f"评测样本 {case.id} 的章节标题与标注不一致。")

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
        candidate = _clone(screenplay)
        started = time.perf_counter()
        try:
            results["fixed_pipeline"] = _fixed_pipeline(
                candidate, case, mode, threshold, pricing
            )
        except Exception as exc:
            results["fixed_pipeline"] = _failed_variant(
                "fixed_pipeline",
                candidate,
                case,
                exc,
                int((time.perf_counter() - started) * 1000),
                pricing,
            )
    if "single_agent" in variants:
        candidate = _clone(screenplay)
        started = time.perf_counter()
        try:
            results["single_agent"] = _single_agent(
                candidate, case, mode, threshold, max_steps, knowledge, pricing
            )
        except Exception as exc:
            results["single_agent"] = _failed_variant(
                "single_agent",
                candidate,
                case,
                exc,
                int((time.perf_counter() - started) * 1000),
                pricing,
            )
    if "multi_agent" in variants:
        candidate = _clone(screenplay)
        started = time.perf_counter()
        try:
            results["multi_agent"] = _multi_agent(
                candidate,
                case,
                mode,
                threshold,
                max_steps,
                max_rounds,
                knowledge,
                pricing,
            )
        except Exception as exc:
            results["multi_agent"] = _failed_variant(
                "multi_agent",
                candidate,
                case,
                exc,
                int((time.perf_counter() - started) * 1000),
                pricing,
            )
    return CaseReport(
        case_id=case.id,
        title=case.title,
        split=split,
        sample_index=sample_index,
        conversion_duration_ms=conversion_duration_ms,
        conversion_llm_calls=conversion_usage["llm_calls"],
        conversion_prompt_tokens=conversion_usage["prompt_tokens"],
        conversion_completion_tokens=conversion_usage["completion_tokens"],
        conversion_total_tokens=conversion_usage["total_tokens"],
        conversion_estimated_cost=_estimated_cost(conversion_usage, pricing),
        conversion_warnings=list(converter.last_run_warnings),
        source=source,
        variants=results,
        source_text=case.novel_text,
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


def _summarize(
    cases: list[CaseReport],
    variants: tuple[str, ...],
    attempted_run_count: int | None = None,
) -> dict:
    boundaries = _aggregate_counts([case.source.scene_boundaries for case in cases])
    continuity = _aggregate_counts([case.source.continuity_probe for case in cases])
    summary: dict = {
        "case_count": len(cases),
        "attempted_run_count": attempted_run_count or len(cases),
        "failed_run_count": (attempted_run_count or len(cases)) - len(cases),
        "run_success_rate": round(
            len(cases) / (attempted_run_count or len(cases)), 4
        )
        if (attempted_run_count or len(cases))
        else 0.0,
        "unique_case_count": len({case.case_id for case in cases}),
        "repeat_count": max((case.sample_index for case in cases), default=0),
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
        "conversion": {
            "total_llm_calls": sum(case.conversion_llm_calls for case in cases),
            "total_prompt_tokens": sum(case.conversion_prompt_tokens for case in cases),
            "total_completion_tokens": sum(
                case.conversion_completion_tokens for case in cases
            ),
            "total_tokens": sum(case.conversion_total_tokens for case in cases),
            "total_estimated_cost": (
                round(
                    sum(
                        case.conversion_estimated_cost or 0.0 for case in cases
                    ),
                    8,
                )
                if any(case.conversion_estimated_cost is not None for case in cases)
                else None
            ),
            "latency": summarize_values(
                [case.conversion_duration_ms for case in cases], lower_bound=0.0
            ),
        },
    }
    for variant in variants:
        attempted_rows = [
            case.variants[variant] for case in cases if variant in case.variants
        ]
        rows = [row for row in attempted_rows if row.status != "failed"]
        failed_count = len(attempted_rows) - len(rows)
        characters = _aggregate_counts([row.output.characters for row in rows])
        dialogue = _aggregate_attribution(
            [row.output.dialogue_attribution for row in rows]
        )
        action_count = sum(row.behavior.action_count for row in attempted_rows)
        invalid_actions = sum(
            row.behavior.invalid_action_count for row in attempted_rows
        )
        repeated_actions = sum(
            row.behavior.repeated_action_count for row in attempted_rows
        )
        passed_scenes = sum(row.pass_count for row in rows)
        reviewed_scenes = passed_scenes + sum(row.fail_count for row in rows)
        goal_count = sum(row.goal_achieved for row in attempted_rows)
        costs = [
            row.estimated_cost
            for row in attempted_rows
            if row.estimated_cost is not None
        ]
        final_score_stats = summarize_values([row.final_avg_score for row in rows])
        goal_stats = summarize_values(
            [row.goal_achieved for row in attempted_rows],
            lower_bound=0.0,
            upper_bound=1.0,
        )
        latency_stats = summarize_values(
            [row.duration_ms for row in attempted_rows], lower_bound=0.0
        )
        token_stats = summarize_values(
            [row.total_tokens for row in attempted_rows], lower_bound=0.0
        )
        summary["variants"][variant] = {
            "run_count": len(attempted_rows),
            "successful_run_count": len(rows),
            "failed_run_count": failed_count,
            "run_success_rate": (
                round(len(rows) / len(attempted_rows), 4) if attempted_rows else 0.0
            ),
            "workflow_completion_rate": _mean(
                [row.status == "completed" for row in attempted_rows]
            ),
            "goal_achieved_rate": _mean(
                [row.goal_achieved for row in attempted_rows]
            ),
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
            "avg_steps_used": _mean([row.steps_used for row in attempted_rows]),
            "avg_rounds_used": _mean([row.rounds_used for row in attempted_rows]),
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
                [row.behavior.circuit_breaker_triggered for row in attempted_rows]
            ),
            "avg_duration_ms": _mean([row.duration_ms for row in attempted_rows]),
            "latency_p50_ms": latency_stats["p50"],
            "latency_p95_ms": latency_stats["p95"],
            "total_llm_calls": sum(row.llm_calls for row in attempted_rows),
            "total_prompt_tokens": sum(row.prompt_tokens for row in attempted_rows),
            "total_completion_tokens": sum(
                row.completion_tokens for row in attempted_rows
            ),
            "total_tokens": sum(row.total_tokens for row in attempted_rows),
            "total_cache_hits": sum(row.cache_hits for row in attempted_rows),
            "total_estimated_cost": round(sum(costs), 8) if costs else None,
            "avg_estimated_cost": _mean(costs) if costs else None,
            "cost_per_goal_achieved": (
                round(sum(costs) / goal_count, 8) if costs and goal_count else None
            ),
            "statistics": {
                "goal_achieved_rate": goal_stats,
                "final_score": final_score_stats,
                "score_delta": summarize_values([row.score_delta for row in rows]),
                "duration_ms": latency_stats,
                "total_tokens": token_stats,
                "estimated_cost": (
                    summarize_values(costs, lower_bound=0.0) if costs else None
                ),
            },
            "dialogue_counts": dialogue,
        }
    single = summary["variants"].get("single_agent")
    team = summary["variants"].get("multi_agent")
    if (
        single is not None
        and team is not None
        and single["successful_run_count"]
        and team["successful_run_count"]
    ):
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
            "llm_call_ratio": (
                round(int(team["total_llm_calls"]) / int(single["total_llm_calls"]), 4)
                if int(single["total_llm_calls"])
                else None
            ),
            "cost_ratio": (
                round(
                    float(team["total_estimated_cost"])
                    / float(single["total_estimated_cost"]),
                    4,
                )
                if single["total_estimated_cost"]
                and team["total_estimated_cost"] is not None
                else None
            ),
        }
    return summary


@contextmanager
def _evaluation_environment(
    mode: str, temperature: float | None, cache_enabled: bool
):
    updates: dict[str, str] = {}
    if mode == "ai":
        updates[CACHE_DISABLE_ENV] = "0" if cache_enabled else "1"
        if temperature is not None:
            updates["AI_TEMPERATURE"] = str(temperature)
    previous = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _runtime_metadata(mode: str) -> dict:
    if mode == "demo":
        return {
            "model": "demo-rules",
            "provider": "local",
            "wire_api": "none",
            "temperature": None,
            "reasoning_effort": "",
            "max_concurrency": 1,
        }
    client = LLMClient()
    base_url = client.base_url
    reasoning_effort = client.reasoning_effort
    effective_temperature = client.temperature
    if client.wire_api == "responses" and reasoning_effort.lower() not in {"", "none"}:
        effective_temperature = None
    return {
        "model": client.model,
        "provider": urlsplit(base_url).hostname or base_url,
        "wire_api": client.wire_api,
        "temperature": effective_temperature,
        "reasoning_effort": reasoning_effort,
        "max_concurrency": client.max_concurrency,
    }


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


def _case_fingerprint(case: EvalCase) -> str:
    payload = json.dumps(
        case.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dataset_case_config(dataset, case: EvalCase) -> dict:
    return {
        "version": dataset.version,
        "split": dataset.split,
        "case_id": case.id,
        "case_fingerprint": _case_fingerprint(case),
    }


def _checkpoint_case_payload(case: CaseReport) -> dict:
    payload = case.model_dump(mode="json")
    payload["source_text"] = case.source_text
    for name, row in case.variants.items():
        payload["variants"][name]["screenplay"] = (
            row.screenplay.model_dump(mode="json") if row.screenplay is not None else None
        )
    return payload


def _write_checkpoint(
    path: Path,
    config: dict,
    cases: list[CaseReport],
    failures: list[EvalFailure],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "config": config,
        "cases": [_checkpoint_case_payload(case) for case in cases],
        "failures": [failure.model_dump(mode="json") for failure in failures],
    }
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_checkpoint(
    path: Path, expected_config: dict
) -> tuple[list[CaseReport], list[EvalFailure]]:
    config, cases, failures = _read_checkpoint(path)
    if config != expected_config:
        raise ValueError("评测 checkpoint 配置与本次运行不一致，不能恢复。")
    return cases, failures


def _read_checkpoint(path: Path) -> tuple[dict, list[CaseReport], list[EvalFailure]]:
    if not path.is_file():
        raise ValueError(f"找不到评测 checkpoint：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"评测 checkpoint 无法读取：{path}") from exc
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError(f"评测 checkpoint 版本不受支持：{path}")
    config = payload.get("config")
    if not isinstance(config, dict):
        raise ValueError(f"评测 checkpoint 缺少运行配置：{path}")
    try:
        cases = [CaseReport.model_validate(item) for item in payload.get("cases", [])]
        failures = [
            EvalFailure.model_validate(item) for item in payload.get("failures", [])
        ]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"评测 checkpoint 内容无效：{path}") from exc
    return config, cases, failures


def _shared_checkpoint_config(config: dict) -> dict:
    return {key: value for key, value in config.items() if key != "datasets"}


def merge_checkpoints(
    dataset_paths: list[str | Path], checkpoint_paths: list[str | Path]
) -> EvalReport:
    if not checkpoint_paths:
        raise ValueError("至少需要一个评测 checkpoint。")

    datasets = load_datasets(dataset_paths)
    expected_descriptors = [
        _dataset_case_config(dataset, case)
        for dataset in datasets
        for case in dataset.cases
    ]
    expected_by_id = {item["case_id"]: item for item in expected_descriptors}
    case_order = {
        descriptor["case_id"]: index
        for index, descriptor in enumerate(expected_descriptors)
    }

    shared_config: dict | None = None
    selected_case_ids: set[str] = set()
    cases: list[CaseReport] = []
    failures: list[EvalFailure] = []
    for raw_path in checkpoint_paths:
        path = Path(raw_path)
        config, shard_cases, shard_failures = _read_checkpoint(path)
        current_shared = _shared_checkpoint_config(config)
        if shared_config is None:
            shared_config = current_shared
        elif current_shared != shared_config:
            raise ValueError(f"checkpoint 公共运行配置不一致：{path}")

        descriptors = config.get("datasets")
        if not isinstance(descriptors, list) or not descriptors:
            raise ValueError(f"checkpoint 未声明评测 case：{path}")
        shard_case_ids: set[str] = set()
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise ValueError(f"checkpoint case 配置无效：{path}")
            case_id = str(descriptor.get("case_id", ""))
            if descriptor != expected_by_id.get(case_id):
                raise ValueError(f"checkpoint case 与当前数据集不一致：{case_id or path}")
            if case_id in selected_case_ids:
                raise ValueError(f"多个 checkpoint 重复包含 case：{case_id}")
            selected_case_ids.add(case_id)
            shard_case_ids.add(case_id)
        shard_result_ids = {
            row.case_id for row in [*shard_cases, *shard_failures]
        }
        if not shard_result_ids <= shard_case_ids:
            raise ValueError(f"checkpoint 结果超出其声明的 case：{path}")
        cases.extend(shard_cases)
        failures.extend(shard_failures)

    if shared_config is None:
        raise ValueError("没有可合并的 checkpoint 配置。")
    current_commit = _git_commit()
    if not current_commit or shared_config.get("git_commit") != current_commit:
        raise ValueError("checkpoint 的 Git 提交与当前工作区不一致。")
    missing_case_ids = set(expected_by_id) - selected_case_ids
    if missing_case_ids:
        raise ValueError(
            f"checkpoint 缺少 case：{', '.join(sorted(missing_case_ids))}"
        )
    unexpected_case_ids = selected_case_ids - set(expected_by_id)
    if unexpected_case_ids:
        raise ValueError(
            f"checkpoint 包含未知 case：{', '.join(sorted(unexpected_case_ids))}"
        )

    repeats = int(shared_config["repeats"])
    expected_keys = {
        (case_id, sample_index)
        for case_id in expected_by_id
        for sample_index in range(1, repeats + 1)
    }
    completed_rows = [
        (case.case_id, case.sample_index) for case in cases
    ] + [
        (failure.case_id, failure.sample_index) for failure in failures
    ]
    completed_keys = set(completed_rows)
    if len(completed_keys) != len(completed_rows):
        raise ValueError("checkpoint 包含重复的 case/sample 结果。")
    missing_keys = expected_keys - completed_keys
    if missing_keys:
        formatted = ", ".join(
            f"{case_id}/sample-{sample_index}"
            for case_id, sample_index in sorted(missing_keys)
        )
        raise ValueError(f"checkpoint 尚未完成全部运行：{formatted}")
    unexpected_keys = completed_keys - expected_keys
    if unexpected_keys:
        raise ValueError("checkpoint 包含本次数据集之外的 case/sample 结果。")

    def sort_key(row: CaseReport | EvalFailure) -> tuple[int, int]:
        return row.sample_index, case_order[row.case_id]

    cases.sort(key=sort_key)
    failures.sort(key=sort_key)
    variants = tuple(shared_config["variants"])
    runtime = shared_config["runtime"]
    pricing_payload = shared_config.get("pricing")
    pricing = TokenPricing.model_validate(pricing_payload) if pricing_payload else None
    summary = _summarize(cases, variants, attempted_run_count=len(expected_keys))
    return EvalReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=str(shared_config.get("git_commit", "")),
        dataset_versions=sorted({dataset.version for dataset in datasets}),
        splits=sorted({dataset.split for dataset in datasets}),
        mode=shared_config["mode"],
        model=runtime["model"],
        provider=runtime["provider"],
        wire_api=runtime["wire_api"],
        temperature=runtime["temperature"],
        reasoning_effort=runtime["reasoning_effort"],
        repeats=repeats,
        cache_enabled=shared_config["cache_enabled"],
        pricing=pricing,
        threshold=shared_config["threshold"],
        max_steps=shared_config["max_steps"],
        max_rounds=shared_config["max_rounds"],
        execution=ExecutionMetadata(
            strategy="case_shards",
            process_count=len(checkpoint_paths),
            max_concurrency_per_process=int(runtime["max_concurrency"]),
        ),
        cases=cases,
        failures=failures,
        summary=summary,
        prompt_versions=shared_config["prompt_versions"],
    )


def evaluate_datasets(
    dataset_paths: list[str | Path],
    mode: str = "demo",
    variants: tuple[str, ...] = VARIANTS,
    threshold: float = 8.9,
    max_steps: int = 12,
    max_rounds: int = 6,
    repeats: int = 1,
    temperature: float | None = None,
    cache_enabled: bool | None = None,
    pricing: TokenPricing | None = None,
    case_ids: set[str] | None = None,
    progress_cb: Callable[[int, int, str, int, str], None] | None = None,
    checkpoint_path: str | Path | None = None,
    resume: bool = False,
) -> EvalReport:
    if mode not in {"demo", "ai"}:
        raise ValueError(f"不支持的评测模式：{mode}")
    unknown_variants = set(variants) - set(VARIANTS)
    if unknown_variants:
        raise ValueError(f"不支持的评测变体：{', '.join(sorted(unknown_variants))}")
    if not variants:
        raise ValueError("至少需要一个评测变体。")
    if repeats < 1:
        raise ValueError("repeats 必须至少为 1。")
    if resume and checkpoint_path is None:
        raise ValueError("resume=True 时必须提供 checkpoint_path。")

    datasets = load_datasets(dataset_paths)
    selected = [
        (dataset, case)
        for dataset in datasets
        for case in dataset.cases
        if case_ids is None or case.id in case_ids
    ]
    if case_ids is not None:
        found = {case.id for _dataset, case in selected}
        missing = case_ids - found
        if missing:
            raise ValueError(f"评测数据集中不存在 case：{', '.join(sorted(missing))}")
    if not selected:
        raise ValueError("没有可运行的评测样本。")

    resolved_cache_enabled = mode != "ai" if cache_enabled is None else cache_enabled
    if mode == "ai" and repeats > 1 and resolved_cache_enabled:
        raise ValueError("AI 重复采样时不能启用 LLM 缓存。")
    total_runs = repeats * len(selected)
    with _evaluation_environment(mode, temperature, resolved_cache_enabled):
        runtime = _runtime_metadata(mode)
        prompt_versions = current_prompt_versions()
        checkpoint_config = {
            "datasets": [
                _dataset_case_config(dataset, case)
                for dataset, case in selected
            ],
            "git_commit": _git_commit(),
            "mode": mode,
            "variants": list(variants),
            "threshold": threshold,
            "max_steps": max_steps,
            "max_rounds": max_rounds,
            "repeats": repeats,
            "cache_enabled": resolved_cache_enabled,
            "pricing": pricing.model_dump(mode="json") if pricing is not None else None,
            "runtime": runtime,
            "prompt_versions": prompt_versions,
        }
        resolved_checkpoint = Path(checkpoint_path) if checkpoint_path else None
        if resume:
            cases, failures = _load_checkpoint(
                resolved_checkpoint, checkpoint_config
            )
        else:
            cases, failures = [], []
            if resolved_checkpoint is not None:
                _write_checkpoint(
                    resolved_checkpoint, checkpoint_config, cases, failures
                )

        expected_keys = {
            (case.id, sample_index)
            for sample_index in range(1, repeats + 1)
            for _dataset, case in selected
        }
        completed_keys = {
            (case.case_id, case.sample_index) for case in cases
        } | {
            (failure.case_id, failure.sample_index) for failure in failures
        }
        if not completed_keys <= expected_keys:
            raise ValueError("评测 checkpoint 包含本次运行之外的 case。")
        if len(completed_keys) != len(cases) + len(failures):
            raise ValueError("评测 checkpoint 包含重复 case。")
        completed_runs = len(completed_keys)
        if completed_runs and progress_cb is not None:
            progress_cb(completed_runs, total_runs, "checkpoint", 0, "resumed")

        for sample_index in range(1, repeats + 1):
            for dataset, case in selected:
                key = (case.id, sample_index)
                if key in completed_keys:
                    continue
                try:
                    result = _evaluate_case(
                        case,
                        dataset.split,
                        mode,
                        variants,
                        threshold,
                        max_steps,
                        max_rounds,
                        sample_index,
                        pricing,
                    )
                except EvaluationDataError:
                    raise
                except Exception as exc:
                    failures.append(
                        EvalFailure(
                            case_id=case.id,
                            title=case.title,
                            split=dataset.split,
                            sample_index=sample_index,
                            stage="conversion",
                            error=redact_secrets(str(exc))[:500],
                        )
                    )
                    completed_runs += 1
                    completed_keys.add(key)
                    if resolved_checkpoint is not None:
                        _write_checkpoint(
                            resolved_checkpoint,
                            checkpoint_config,
                            cases,
                            failures,
                        )
                    if progress_cb is not None:
                        progress_cb(
                            completed_runs,
                            total_runs,
                            case.id,
                            sample_index,
                            "conversion_failed",
                        )
                    continue
                cases.append(result)
                completed_runs += 1
                completed_keys.add(key)
                if resolved_checkpoint is not None:
                    _write_checkpoint(
                        resolved_checkpoint, checkpoint_config, cases, failures
                    )
                if progress_cb is not None:
                    statuses = ", ".join(
                        f"{name}={row.status}"
                        for name, row in result.variants.items()
                    )
                    progress_cb(
                        completed_runs,
                        total_runs,
                        case.id,
                        sample_index,
                        statuses,
                    )

    return EvalReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=_git_commit(),
        dataset_versions=sorted({dataset.version for dataset in datasets}),
        splits=sorted({dataset.split for dataset in datasets}),
        mode=mode,
        model=runtime["model"],
        provider=runtime["provider"],
        wire_api=runtime["wire_api"],
        temperature=runtime["temperature"],
        reasoning_effort=runtime["reasoning_effort"],
        repeats=repeats,
        cache_enabled=resolved_cache_enabled,
        pricing=pricing,
        threshold=threshold,
        max_steps=max_steps,
        max_rounds=max_rounds,
        execution=ExecutionMetadata(
            strategy="serial",
            process_count=1,
            max_concurrency_per_process=int(runtime["max_concurrency"]),
        ),
        cases=cases,
        failures=failures,
        summary=_summarize(cases, variants, attempted_run_count=len(selected) * repeats),
        prompt_versions=prompt_versions,
    )
