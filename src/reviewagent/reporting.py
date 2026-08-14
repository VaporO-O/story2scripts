"""JSON and Markdown output for code review reports."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ReviewReport
from .security import validate_report_prefix


def render_review_markdown(report: ReviewReport) -> str:
    lines = [
        "# Code Review Agent Report",
        "",
        f"- Thread: `{report.thread_id}`",
        f"- Generated: `{report.generated_at}`",
        f"- Range: `{report.base_ref}...{report.head_ref}`",
        f"- Mode / status: `{report.mode}` / `{report.status}`",
        f"- Verdict: `{report.summary.verdict}`",
        f"- Tools: `{', '.join(report.planned_tools)}`",
        "",
        report.summary.overview,
        "",
        "## Findings",
        "",
    ]
    if not report.summary.findings:
        lines.append("No actionable findings.")
    for finding in report.summary.findings:
        location = finding.file or "repository"
        if finding.line:
            location += f":{finding.line}"
        lines.extend(
            [
                f"### [{finding.severity.upper()}] {finding.title}",
                "",
                f"- Source / rule: `{finding.source}` / `{finding.rule_id}`",
                f"- Location: `{location}`",
                f"- Fingerprint: `{finding.fingerprint}`",
                "",
                finding.message,
                "",
            ]
        )
        if finding.evidence:
            lines.extend(["```text", finding.evidence, "```", ""])

    lines.extend(
        [
            "## Tool Results",
            "",
            "| Tool | Status | Exit | Duration | Findings |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for result in report.tool_results:
        exit_code = "-" if result.exit_code is None else str(result.exit_code)
        lines.append(
            f"| {result.tool} | {result.status} | {exit_code} | "
            f"{result.duration_ms} ms | {len(result.findings)} |"
        )

    if report.decision is not None:
        lines.extend(
            [
                "",
                "## Human Decision",
                "",
                f"- Approved: `{'yes' if report.decision.approved else 'no'}`",
                f"- Comment: {report.decision.comment or '-'}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_review_reports(
    report: ReviewReport,
    output_dir: str | Path,
    prefix: str | None = None,
) -> tuple[Path, Path]:
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    name = validate_report_prefix(prefix or report.thread_id)
    json_path = target / f"{name}.json"
    markdown_path = target / f"{name}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_review_markdown(report), encoding="utf-8")
    return json_path, markdown_path
