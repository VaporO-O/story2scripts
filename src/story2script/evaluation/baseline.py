"""版本化评测基线与回归门禁。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvalBaseline, EvalReport, GateResult


def load_baseline(path: str | Path) -> EvalBaseline:
    resolved = Path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取评测基线 {resolved}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"评测基线不是合法 JSON：{resolved}（{exc}）") from exc
    return EvalBaseline.model_validate(payload)


def _resolve(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def apply_baseline(report: EvalReport, baseline: EvalBaseline) -> list[GateResult]:
    if report.mode != baseline.mode:
        raise ValueError(
            f"评测模式 {report.mode} 与基线模式 {baseline.mode} 不一致。"
        )
    missing_versions = set(baseline.dataset_versions) - set(report.dataset_versions)
    if missing_versions:
        raise ValueError(
            "评测报告缺少基线要求的数据集版本：" + ", ".join(sorted(missing_versions))
        )

    payload = report.model_dump(mode="json")
    results: list[GateResult] = []
    for gate in baseline.gates:
        try:
            actual = _resolve(payload, gate.path)
        except KeyError:
            results.append(
                GateResult(
                    name=gate.name,
                    path=gate.path,
                    passed=False,
                    actual=None,
                    expected="指标存在",
                    message="报告中缺少该指标。",
                )
            )
            continue

        if gate.minimum is not None:
            passed = float(actual) >= gate.minimum
            expected = f">= {gate.minimum}"
        elif gate.maximum is not None:
            passed = float(actual) <= gate.maximum
            expected = f"<= {gate.maximum}"
        else:
            passed = actual == gate.equals
            expected = f"== {gate.equals}"
        results.append(
            GateResult(
                name=gate.name,
                path=gate.path,
                passed=passed,
                actual=actual,
                expected=expected,
                message="" if passed else f"实际值 {actual} 未满足 {expected}。",
            )
        )
    report.gates = results
    return results
