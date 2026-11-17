"""``python -m story2script.evaluation`` 命令入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .baseline import apply_baseline, load_baseline
from .models import TokenPricing
from .pairwise import score_blind_review_files, write_blind_review_files
from .reporting import write_reports
from .runner import VARIANTS, evaluate_datasets, merge_checkpoints


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
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--temperature", type=float)
    run.add_argument("--allow-cache", action="store_true")
    run.add_argument("--case", action="append", help="只运行指定 case id，可重复")
    run.add_argument("--input-cost-per-million", type=float)
    run.add_argument("--output-cost-per-million", type=float)
    run.add_argument("--currency", default="USD")
    run.add_argument("--write-blind-review", action="store_true")
    run.add_argument("--blind-seed", type=int, default=2025)
    run.add_argument("--baseline", help="可选的回归门禁 JSON")
    run.add_argument("--output-dir", default="evals/reports")
    run.add_argument("--report-prefix", default="demo-latest")
    run.add_argument("--checkpoint", help="逐 case 保存的 checkpoint JSON")
    run.add_argument("--resume", action="store_true", help="从 checkpoint 继续运行")
    run.add_argument("--fail-on-regression", action="store_true")

    merge = subparsers.add_parser(
        "merge-checkpoints", help="合并按 case 分片运行的 checkpoint"
    )
    merge.add_argument("--dataset", action="append", required=True, help="完整数据集 JSON")
    merge.add_argument(
        "--checkpoint", action="append", required=True, help="待合并 checkpoint，可重复"
    )
    merge.add_argument("--output-dir", default="evals/reports")
    merge.add_argument("--report-prefix", default="merged-latest")
    merge.add_argument("--write-blind-review", action="store_true")
    merge.add_argument("--blind-seed", type=int, default=2025)

    score = subparsers.add_parser("score-pairwise", help="汇总人工 A/B 盲测答卷")
    score.add_argument("--responses", required=True)
    score.add_argument("--key", required=True)
    score.add_argument("--output", required=True)
    audit = subparsers.add_parser("audit-dialogue", help="分析已保存的 AI 剧本对白归属")
    audit.add_argument("--checkpoint", action="append", required=True)
    audit.add_argument("--dataset", action="append", required=True)
    audit.add_argument("--output", required=True, help="JSON/Markdown 输出前缀")
    retrieval = subparsers.add_parser("rag", help="独立检索评测")
    retrieval.add_argument("--dataset", action="append", required=True)
    retrieval.add_argument("--variants", default="lexical")
    retrieval.add_argument("--mode", choices=("demo", "ai"), default="demo")
    retrieval.add_argument("--top-k", type=int, default=5)
    retrieval.add_argument("--answers", action="store_true")
    retrieval.add_argument("--min-recall", type=float)
    retrieval.add_argument("--min-mrr", type=float)
    retrieval.add_argument("--output", default="evals/reports/rag-latest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "rag":
        from .retrieval import run_retrieval_eval
        if not 1 <= args.top_k <= 20:
            raise SystemExit("top-k 必须在 1 到 20 之间")
        if any(value is not None and not 0 <= value <= 1
               for value in (args.min_recall, args.min_mrr)):
            raise SystemExit("检索门禁阈值必须在 0 到 1 之间")
        report = run_retrieval_eval(args.dataset, args.variants.split(","), args.mode,
                                    args.top_k, args.output, answers=args.answers)
        print(report["summary"])
        failed = any(row["status"] != "completed" for row in report["summary"].values())
        for variant in report["summary"].values():
            for split, row in variant.get("splits", {}).items():
                for key, minimum in ((f"recall_at_{args.top_k}", args.min_recall), ("mrr", args.min_mrr)):
                    if minimum is not None and (row[key] is None or row[key] < minimum):
                        print(f"检索门禁失败：{split} {key}={row[key]} < {minimum}")
                        failed = True
        return int(failed)
    if args.command == "audit-dialogue":
        from .dialogue_audit import audit_checkpoints
        report = audit_checkpoints(args.checkpoint, args.dataset, args.output)
        print(report["summary"])
        return 0
    if args.command == "score-pairwise":
        path = score_blind_review_files(args.responses, args.key, args.output)
        print(f"Pairwise summary: {path}")
        return 0

    if args.command == "merge-checkpoints":
        report = merge_checkpoints(
            [Path(path) for path in args.dataset],
            [Path(path) for path in args.checkpoint],
        )
        json_path, markdown_path = write_reports(
            report, args.output_dir, prefix=args.report_prefix
        )
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {markdown_path}")
        if args.write_blind_review:
            review_path, responses_path, key_path = write_blind_review_files(
                report,
                args.output_dir,
                args.report_prefix,
                seed=args.blind_seed,
            )
            print(f"Blind review: {review_path}")
            print(f"Blind responses: {responses_path}")
            print(f"Blind key: {key_path}")
        return 0

    variants = tuple(item.strip() for item in args.variants.split(",") if item.strip())
    price_values = (args.input_cost_per_million, args.output_cost_per_million)
    if any(value is not None for value in price_values) and not all(
        value is not None for value in price_values
    ):
        raise SystemExit("input/output cost must be provided together")
    pricing = (
        TokenPricing(
            currency=args.currency.upper(),
            input_per_million=args.input_cost_per_million,
            output_per_million=args.output_cost_per_million,
        )
        if all(value is not None for value in price_values)
        else None
    )
    if args.write_blind_review and not all(
        name in variants for name in ("single_agent", "multi_agent")
    ):
        raise SystemExit("blind review requires single_agent and multi_agent")
    def report_progress(
        completed: int, total: int, case_id: str, sample_index: int, status: str
    ) -> None:
        if case_id == "checkpoint":
            print(f"[{completed}/{total}] checkpoint: {status}", flush=True)
            return
        print(
            f"[{completed}/{total}] {case_id} sample {sample_index}: {status}",
            flush=True,
        )

    report = evaluate_datasets(
        [Path(path) for path in args.dataset],
        mode=args.mode,
        variants=variants,
        threshold=args.threshold,
        max_steps=args.max_steps,
        max_rounds=args.max_rounds,
        repeats=args.repeats,
        temperature=args.temperature,
        cache_enabled=args.allow_cache if args.mode == "ai" else True,
        pricing=pricing,
        case_ids=set(args.case) if args.case else None,
        progress_cb=report_progress,
        checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
        resume=args.resume,
    )
    if args.baseline:
        apply_baseline(report, load_baseline(args.baseline))
    json_path, markdown_path = write_reports(
        report, args.output_dir, prefix=args.report_prefix
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if args.write_blind_review:
        review_path, responses_path, key_path = write_blind_review_files(
            report,
            args.output_dir,
            args.report_prefix,
            seed=args.blind_seed,
        )
        print(f"Blind review: {review_path}")
        print(f"Blind responses: {responses_path}")
        print(f"Blind key: {key_path}")
    failed = [gate for gate in report.gates if not gate.passed]
    if failed:
        print(f"Regression gates failed: {len(failed)}")
    elif report.gates:
        print(f"Regression gates passed: {len(report.gates)}")
    return 1 if failed and args.fail_on_regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
