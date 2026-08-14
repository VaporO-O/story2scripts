"""Command line entry point for the code review agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import ApprovalDecision, DEFAULT_TOOLS, ReviewRequest
from .reporting import write_review_reports


DEFAULT_CHECKPOINT = Path(".reviewagent") / "review.sqlite3"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangGraph code review agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run review tools and pause for approval")
    run.add_argument("--repo", default=".")
    run.add_argument("--base", required=True, help="Base Git revision")
    run.add_argument("--head", default="HEAD", help="Head Git revision (default: HEAD)")
    run.add_argument("--mode", choices=("demo", "ai"), default="demo")
    run.add_argument("--tool", action="append", choices=DEFAULT_TOOLS)
    run.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    run.add_argument("--thread-id")
    run.add_argument("--output-dir", default=".reviewagent/reports")
    run.add_argument("--report-prefix")
    run.add_argument("--max-output-chars", type=int, default=50_000)
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument(
        "--pytest-target",
        action="append",
        help="Limit pytest to a safe repository-relative path; repeatable",
    )
    run.add_argument("--pytest-collect-only", action="store_true")
    run.add_argument(
        "--pytest-import-mode",
        choices=("prepend", "append", "importlib"),
        default="prepend",
    )
    run.add_argument(
        "--allow-historical-head",
        action="store_true",
        help="Allow head to differ from the current checkout",
    )

    resume = subparsers.add_parser("resume", help="Resume a review at the approval gate")
    resume.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    resume.add_argument("--thread-id", required=True)
    decision = resume.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    resume.add_argument("--comment", default="")
    resume.add_argument("--output-dir", default=".reviewagent/reports")
    resume.add_argument("--report-prefix")

    evaluate = subparsers.add_parser("eval", help="Run the versioned review evaluation set")
    evaluate.add_argument(
        "--dataset", default="evals/reviewagent/v1/cases.json"
    )
    evaluate.add_argument("--repo", default=".")
    evaluate.add_argument("--output-dir", default="evals/reports")
    evaluate.add_argument("--report-prefix", default="reviewagent-latest")
    evaluate.add_argument("--timeout", type=int, default=300)
    evaluate.add_argument("--min-recall", type=float, default=1.0)
    evaluate.add_argument("--max-false-positive-rate", type=float, default=0.0)
    return parser


def _run(args: argparse.Namespace) -> int:
    from .runner import start_review

    request = ReviewRequest(
        repo_path=args.repo,
        base_ref=args.base,
        head_ref=args.head,
        mode=args.mode,
        tools=args.tool or list(DEFAULT_TOOLS),
        max_output_chars=args.max_output_chars,
        timeout_seconds=args.timeout,
        allow_historical_head=args.allow_historical_head,
        pytest_targets=args.pytest_target or [],
        pytest_collect_only=args.pytest_collect_only,
        pytest_import_mode=args.pytest_import_mode,
    )
    result = start_review(
        request,
        checkpoint_path=args.checkpoint,
        thread_id=args.thread_id,
    )
    json_path, markdown_path = write_review_reports(
        result.report, args.output_dir, args.report_prefix
    )
    print(f"Thread: {result.thread_id}")
    print(f"Status: {result.report.status}")
    print(f"Findings: {len(result.report.summary.findings)}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if result.awaiting_approval:
        print(
            "Resume with: story2script-review resume "
            f'--checkpoint "{args.checkpoint}" --thread-id {result.thread_id} --approve'
        )
    return 0


def _resume(args: argparse.Namespace) -> int:
    from .runner import resume_review

    result = resume_review(
        checkpoint_path=args.checkpoint,
        thread_id=args.thread_id,
        decision=ApprovalDecision(approved=args.approve, comment=args.comment),
    )
    json_path, markdown_path = write_review_reports(
        result.report, args.output_dir, args.report_prefix
    )
    print(f"Thread: {result.thread_id}")
    print(f"Status: {result.report.status}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    from .evaluation import load_review_dataset, run_review_evaluation
    from .evaluation import write_evaluation_reports

    if not 0.0 <= args.min_recall <= 1.0:
        raise ValueError("min-recall must be between 0 and 1.")
    if not 0.0 <= args.max_false_positive_rate <= 1.0:
        raise ValueError("max-false-positive-rate must be between 0 and 1.")
    dataset = load_review_dataset(args.dataset)
    report = run_review_evaluation(
        dataset,
        repo_path=Path(args.repo),
        timeout_seconds=args.timeout,
    )
    json_path, markdown_path = write_evaluation_reports(
        report, args.output_dir, args.report_prefix
    )
    print(f"Recall: {report.summary.recall:.3f}")
    print(f"False positive rate: {report.summary.false_positive_rate:.3f}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    failed = report.summary.failed_case_count > 0
    recall_failed = report.summary.recall < args.min_recall
    false_positive_failed = (
        report.summary.false_positive_rate > args.max_false_positive_rate
    )
    if failed or recall_failed or false_positive_failed:
        print("Evaluation gates failed.")
        return 1
    print("Evaluation gates passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "resume":
            return _resume(args)
        return _evaluate(args)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("langgraph"):
            print('error: Review Agent dependencies are missing; install ".[review]".', file=sys.stderr)
            return 2
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
