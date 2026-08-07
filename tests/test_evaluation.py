import json
from pathlib import Path

import pytest

import story2script.evaluation.runner as eval_runner
from story2script.evaluation import (
    apply_baseline,
    evaluate_datasets,
    load_baseline,
    load_dataset,
    score_blind_reviews,
    write_blind_review_files,
)
from story2script.evaluation.models import BaselineGate, EvalBaseline, TokenPricing
from story2script.evaluation.reporting import render_markdown, write_reports
from story2script.evaluation.scoring import score_sets
from story2script.evaluation.statistics import summarize_values
from story2script.prompt_catalog import current_prompt_versions


ROOT = Path(__file__).parents[1]
DEV_DATASET = ROOT / "evals" / "datasets" / "v1" / "dev.json"
HOLDOUT_DATASET = ROOT / "evals" / "datasets" / "v1" / "holdout.json"
DEMO_BASELINE = ROOT / "evals" / "baselines" / "demo-v1.json"


def test_versioned_datasets_are_valid_and_disjoint() -> None:
    dev = load_dataset(DEV_DATASET)
    holdout = load_dataset(HOLDOUT_DATASET)

    assert dev.version == holdout.version == "story2script-eval-v1"
    assert dev.split == "dev"
    assert holdout.split == "holdout"
    assert len(dev.cases) + len(holdout.cases) == 10
    assert {case.id for case in dev.cases}.isdisjoint(case.id for case in holdout.cases)


def test_committed_demo_baseline_locks_evaluation_budget() -> None:
    baseline = load_baseline(DEMO_BASELINE)
    gates = {gate.path: gate for gate in baseline.gates}

    assert baseline.dataset_versions == ["story2script-eval-v1"]
    assert gates["threshold"].equals == 8.9
    assert gates["max_steps"].equals == 12
    assert gates["max_rounds"].equals == 6


def test_score_sets_handles_matches_and_empty_sets() -> None:
    score = score_sets({"a", "b"}, {"b", "c"})
    assert score.correct == 1
    assert score.precision == 0.5
    assert score.recall == 0.5
    assert score.f1 == 0.5

    empty = score_sets(set(), set())
    assert empty.precision == empty.recall == empty.f1 == 1.0


def test_prompt_catalog_has_stable_source_fingerprints() -> None:
    versions = current_prompt_versions()

    assert len(versions) == 8
    assert set(versions) >= {
        "conversion.chapter_chunk",
        "agent.planner",
        "agent.team_supervisor",
        "continuity.arc_review",
    }
    assert all(value.startswith("v1:sha256:") for value in versions.values())


def test_statistics_include_percentiles_and_confidence_interval() -> None:
    stats = summarize_values([1, 2, 3])

    assert stats["count"] == 3
    assert stats["mean"] == 2.0
    assert stats["p50"] == 2.0
    assert stats["p95"] == 3.0
    assert stats["ci95_low"] < stats["mean"] < stats["ci95_high"]


def _single_case_dataset(tmp_path: Path) -> Path:
    payload = json.loads(DEV_DATASET.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    path = tmp_path / "single-case.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_runner_compares_all_variants_and_scores_probes(tmp_path: Path) -> None:
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)],
        mode="demo",
        threshold=8.9,
        max_steps=2,
        max_rounds=3,
    )

    assert report.summary["case_count"] == 1
    assert report.summary["source"]["boundary_f1"] == 1.0
    assert report.summary["source"]["continuity_f1"] == 1.0
    assert set(report.cases[0].variants) == {
        "fixed_pipeline",
        "single_agent",
        "multi_agent",
    }
    assert report.summary["variants"]["single_agent"]["tool_legal_rate"] == 1.0
    assert "single_vs_multi" in report.summary
    assert report.report_version == "2"
    assert len(report.prompt_versions) == 8

    review_path, responses_path, key_path = write_blind_review_files(
        report, tmp_path / "reports", "pairwise", seed=7
    )
    packet = json.loads(review_path.read_text(encoding="utf-8"))
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    answer_key = json.loads(key_path.read_text(encoding="utf-8"))
    assert packet["pairs"][0]["candidate_a"]["schema_version"] == "1.0"
    assert "single_agent" not in json.dumps(packet, ensure_ascii=False)

    responses["reviews"][0]["preference"] = "A"
    summary = score_blind_reviews(responses, answer_key)
    winner = answer_key["pairs"][0]["candidate_a"]
    assert summary["wins"][winner] == 1


def test_repeated_runs_report_statistics_and_optional_cost(tmp_path: Path) -> None:
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)],
        variants=("fixed_pipeline",),
        repeats=2,
        pricing=TokenPricing(input_per_million=1.0, output_per_million=2.0),
    )

    assert report.summary["case_count"] == 2
    assert report.summary["unique_case_count"] == 1
    assert report.summary["repeat_count"] == 2
    assert [case.sample_index for case in report.cases] == [1, 2]
    stats = report.summary["variants"]["fixed_pipeline"]["statistics"]
    assert stats["final_score"]["count"] == 2
    assert report.summary["variants"]["fixed_pipeline"]["total_estimated_cost"] == 0.0


def test_checkpoint_resumes_after_completed_case(tmp_path: Path) -> None:
    dataset = _single_case_dataset(tmp_path)
    checkpoint = tmp_path / "eval.checkpoint.json"

    def interrupt_after_first(
        completed: int, total: int, case_id: str, sample_index: int, status: str
    ) -> None:
        if completed == 1:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        evaluate_datasets(
            [dataset],
            variants=("fixed_pipeline",),
            repeats=2,
            checkpoint_path=checkpoint,
            progress_cb=interrupt_after_first,
        )

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(saved["cases"]) == 1
    assert saved["cases"][0]["source_text"]
    assert saved["cases"][0]["variants"]["fixed_pipeline"]["screenplay"]

    progress: list[tuple[int, str]] = []
    report = evaluate_datasets(
        [dataset],
        variants=("fixed_pipeline",),
        repeats=2,
        checkpoint_path=checkpoint,
        resume=True,
        progress_cb=lambda completed, total, case_id, sample_index, status: progress.append(
            (completed, status)
        ),
    )

    assert [case.sample_index for case in report.cases] == [1, 2]
    assert progress[0] == (1, "resumed")
    assert progress[-1][0] == 2


def test_checkpoint_rejects_mismatched_run_config(tmp_path: Path) -> None:
    dataset = _single_case_dataset(tmp_path)
    checkpoint = tmp_path / "eval.checkpoint.json"
    evaluate_datasets(
        [dataset],
        variants=("fixed_pipeline",),
        checkpoint_path=checkpoint,
    )

    with pytest.raises(ValueError, match="配置与本次运行不一致"):
        evaluate_datasets(
            [dataset],
            variants=("fixed_pipeline",),
            repeats=2,
            checkpoint_path=checkpoint,
            resume=True,
        )


def test_variant_failure_is_recorded_without_losing_the_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_variant(*args, **kwargs):
        raise ValueError("temporary provider failure")

    monkeypatch.setattr(eval_runner, "_fixed_pipeline", fail_variant)
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)], variants=("fixed_pipeline",)
    )

    row = report.cases[0].variants["fixed_pipeline"]
    assert row.status == "failed"
    assert row.goal_achieved is False
    assert "temporary provider failure" in row.error
    assert report.summary["run_success_rate"] == 1.0
    variant_summary = report.summary["variants"]["fixed_pipeline"]
    assert variant_summary["successful_run_count"] == 0
    assert variant_summary["failed_run_count"] == 1
    assert variant_summary["run_success_rate"] == 0.0
    assert variant_summary["statistics"]["final_score"]["count"] == 0
    assert "没有成功输出" in render_markdown(report)


def test_conversion_failure_is_recorded_and_other_report_fields_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenConverter:
        def convert(self, *args, **kwargs):
            raise ValueError("conversion unavailable")

    monkeypatch.setattr(eval_runner, "get_converter", lambda mode: BrokenConverter())
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)], variants=("fixed_pipeline",)
    )

    assert report.cases == []
    assert report.summary["attempted_run_count"] == 1
    assert report.summary["failed_run_count"] == 1
    assert report.summary["run_success_rate"] == 0.0
    assert report.failures[0].stage == "conversion"
    assert "conversion unavailable" in render_markdown(report)


def test_ai_runtime_metadata_reads_provider_config_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_API_KEY", "secret-key")
    monkeypatch.setenv("AI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("AI_MODEL", "model-x")
    monkeypatch.setenv("AI_WIRE_API", "responses")
    monkeypatch.setenv("AI_REASONING_EFFORT", "high")
    monkeypatch.setenv("AI_TEMPERATURE", "0.8")

    metadata = eval_runner._runtime_metadata("ai")

    assert metadata == {
        "model": "model-x",
        "provider": "provider.example",
        "wire_api": "responses",
        "temperature": None,
        "reasoning_effort": "high",
    }
    assert "secret" not in json.dumps(metadata)


def test_repeated_ai_evaluation_rejects_cache_before_network(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能启用 LLM 缓存"):
        evaluate_datasets(
            [_single_case_dataset(tmp_path)],
            mode="ai",
            repeats=2,
            cache_enabled=True,
        )


def test_baseline_reports_pass_and_failure(tmp_path: Path) -> None:
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)],
        variants=("fixed_pipeline",),
        threshold=8.9,
    )
    baseline = EvalBaseline(
        dataset_versions=["story2script-eval-v1"],
        gates=[
            BaselineGate(
                name="Schema 合法",
                path="summary.variants.fixed_pipeline.schema_valid_rate",
                minimum=1.0,
            ),
            BaselineGate(
                name="故意失败",
                path="summary.case_count",
                minimum=2,
            ),
        ],
    )

    gates = apply_baseline(report, baseline)
    assert gates[0].passed is True
    assert gates[1].passed is False
    assert "实际值" in gates[1].message


def test_report_writes_json_and_markdown(tmp_path: Path) -> None:
    report = evaluate_datasets(
        [_single_case_dataset(tmp_path)],
        variants=("fixed_pipeline",),
    )

    json_path, markdown_path = write_reports(report, tmp_path / "reports", "smoke")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["report_version"] == "2"
    assert "screenplay" not in json.dumps(payload)
    markdown = markdown_path.read_text(encoding="utf-8")
    assert markdown == render_markdown(report)
    assert "Variant Comparison" in markdown


def test_dataset_rejects_boundary_outside_chapter(tmp_path: Path) -> None:
    payload = json.loads(DEV_DATASET.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    first = payload["cases"][0]
    first["expected"]["scene_boundaries"]["第一章 夜站"] = [99]
    path = tmp_path / "bad-boundary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="超出文本单元范围"):
        evaluate_datasets([path], variants=("fixed_pipeline",))
