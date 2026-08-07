"""评测报告的 JSON 与 Markdown 输出。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvalReport


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# Story2Script Eval Report",
        "",
        f"- 生成时间：`{report.generated_at}`",
        f"- Git：`{report.git_commit or 'unknown'}`",
        f"- 数据集：`{', '.join(report.dataset_versions)}`（{', '.join(report.splits)}）",
        f"- 模式 / 模型：`{report.mode}` / `{report.model}`",
        f"- 质量阈值：`{report.threshold}`",
        "",
        "## Source Analysis",
        "",
        "| 指标 | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: |",
    ]
    source = report.summary["source"]
    lines.extend(
        [
            "| 场景边界 | "
            f"{_percent(source['boundary_precision'])} | "
            f"{_percent(source['boundary_recall'])} | "
            f"{_percent(source['boundary_f1'])} |",
            "| 一致性探针 | "
            f"{_percent(source['continuity_precision'])} | "
            f"{_percent(source['continuity_recall'])} | "
            f"{_percent(source['continuity_f1'])} |",
            "",
            "## Variant Comparison",
            "",
            "| 变体 | 目标达成率 | Schema | 人物 F1 | 对白归属 | 初始分 | 最终分 | 提升 | 工具合法率 | 平均耗时 | Tokens |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, row in report.summary["variants"].items():
        lines.append(
            f"| {name} | {_percent(row['goal_achieved_rate'])} | "
            f"{_percent(row['schema_valid_rate'])} | {_percent(row['character_f1'])} | "
            f"{_percent(row['dialogue_accuracy'])} | {row['avg_initial_score']:.2f} | "
            f"{row['avg_final_score']:.2f} | {row['avg_score_delta']:+.2f} | "
            f"{_percent(row['tool_legal_rate'])} | {row['avg_duration_ms']:.0f} ms | "
            f"{row['total_tokens']} |"
        )

    comparison = report.summary.get("single_vs_multi")
    if comparison:
        lines.extend(
            [
                "",
                "## Single vs Multi Agent",
                "",
                f"- 最终分差（多 Agent - 单 Agent）：`{comparison['final_score_delta']:+.4f}`",
                "- 目标达成率差（多 Agent - 单 Agent）："
                f"`{comparison['goal_achieved_rate_delta']:+.4f}`",
                f"- 延迟倍率（多 Agent / 单 Agent）：`{comparison['latency_ratio']}`",
                f"- Token 倍率（多 Agent / 单 Agent）：`{comparison['token_ratio']}`",
            ]
        )

    lines.extend(["", "## Cases", ""])
    for case in report.cases:
        lines.append(f"### {case.case_id} · {case.title}")
        lines.append("")
        lines.append(
            "- 场景边界 F1："
            f"{_percent(case.source.scene_boundaries.f1)}；一致性探针 F1："
            f"{_percent(case.source.continuity_probe.f1)}"
        )
        for name, row in case.variants.items():
            lines.append(
                f"- `{name}`：{row.status}，{row.initial_avg_score:.2f} → "
                f"{row.final_avg_score:.2f}，{row.duration_ms} ms，{row.total_tokens} tokens"
            )
        lines.append("")

    if report.gates:
        lines.extend(
            [
                "## Regression Gates",
                "",
                "| 门禁 | 实际值 | 要求 | 结果 |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for gate in report.gates:
            lines.append(
                f"| {gate.name} | {gate.actual} | `{gate.expected}` | "
                f"{'PASS' if gate.passed else 'FAIL'} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(
    report: EvalReport, output_dir: str | Path, prefix: str = "demo-latest"
) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{prefix}.json"
    markdown_path = target / f"{prefix}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
