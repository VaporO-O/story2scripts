import json
from pathlib import Path

import pytest

from story2script.evaluation import (
    apply_baseline,
    evaluate_datasets,
    load_baseline,
    load_dataset,
)
from story2script.evaluation.models import BaselineGate, EvalBaseline
from story2script.evaluation.reporting import render_markdown, write_reports
from story2script.evaluation.scoring import score_sets


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

    assert json.loads(json_path.read_text(encoding="utf-8"))["report_version"] == "1"
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
