"""``python -m story2script.evaluation`` 命令入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .baseline import apply_baseline, load_baseline
from .reporting import write_reports
from .runner import VARIANTS, evaluate_datasets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Story2Script 可复现离线评测")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="运行评测并生成 JSON/Markdown 报告")
    run.add_argument("--dataset", action="append", required=True, help="数据集 JSON，可重复")
    run.add_argument("--mode", choices=("demo", "ai"), default="demo")
    run.add_argument(
        "--variants",
        default=",".join(VARIANTS),
        help="逗号分隔：fixed_pipeline,single_agent,multi_agent",
    )
    run.add_argument("--threshold", type=float, default=8.9)
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--max-rounds", type=int, default=6)
    run.add_argument("--baseline", help="可选的回归门禁 JSON")
    run.add_argument("--output-dir", default="evals/reports")
    run.add_argument("--report-prefix", default="demo-latest")
    run.add_argument("--fail-on-regression", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    variants = tuple(item.strip() for item in args.variants.split(",") if item.strip())
    report = evaluate_datasets(
        [Path(path) for path in args.dataset],
        mode=args.mode,
        variants=variants,
        threshold=args.threshold,
        max_steps=args.max_steps,
        max_rounds=args.max_rounds,
    )
    if args.baseline:
        apply_baseline(report, load_baseline(args.baseline))
    json_path, markdown_path = write_reports(
        report, args.output_dir, prefix=args.report_prefix
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    failed = [gate for gate in report.gates if not gate.passed]
    if failed:
        print(f"Regression gates failed: {len(failed)}")
    elif report.gates:
        print(f"Regression gates passed: {len(report.gates)}")
    return 1 if failed and args.fail_on_regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
