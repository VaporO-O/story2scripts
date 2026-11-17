"""Re-score saved AI checkpoints without another model call."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from .dataset import load_dataset
from .scoring import diagnose_dialogue_attribution
from ..screenplay import Screenplay


def audit_checkpoints(checkpoints: list[str], datasets: list[str], output: str) -> dict:
    cases = {case.id: case for path in datasets for case in load_dataset(Path(path)).cases}
    counts = defaultdict(Counter)
    failures = []
    seen = set()
    for path in checkpoints:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for case in payload["cases"]:
            key = (case["case_id"], case.get("sample_index", 1))
            if key in seen:
                raise ValueError(f"重复的 case/sample：{key}")
            seen.add(key)
            expected = cases[case["case_id"]].expected
            for variant, result in case["variants"].items():
                screenplay = result.get("screenplay")
                if screenplay is None:
                    raise ValueError("checkpoint 未包含剧本，不能重新分析对白。")
                rows = diagnose_dialogue_attribution(Screenplay.model_validate(screenplay),
                                                    expected.dialogue_attributions)
                for row in rows:
                    counts[variant][row["status"]] += 1
                    if row["status"] != "correct":
                        failures.append({"case_id": key[0], "sample": key[1],
                                         "variant": variant, **row})
    summary = {}
    for variant, count in counts.items():
        total = sum(count.values())
        matched = count["correct"] + count["wrong_speaker"]
        summary[variant] = {
            **{name: count[name] for name in ("correct", "wrong_speaker", "unmatched")},
            "expected": total, "quote_coverage": matched / total if total else 1,
            "matched_speaker_accuracy": count["correct"] / matched if matched else 0,
        }
    report = {"checkpoints": checkpoints, "case_samples": len(seen),
              "summary": summary, "failures": failures}
    prefix = Path(output)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    lines = ["# 对白失败分析", "", "基于保存的剧本重评，无新增模型调用。",
             "未匹配包含省略和改写，不等于说话人错误。", "",
             "| 方案 | 原句匹配率 | 已匹配归属准确率 | 错配 | 未匹配 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for name, row in summary.items():
        lines.append(f"| {name} | {row['quote_coverage']:.1%} | "
                     f"{row['matched_speaker_accuracy']:.1%} | "
                     f"{row['wrong_speaker']} | {row['unmatched']} |")
    lines += ["", "## 失败样例（前 30 条；完整明细见 JSON）", ""]
    for row in failures[:30]:
        lines.append(f"- {row['case_id']} / {row['variant']} / sample {row['sample']}: "
                     f"{row['status']}，原说话人 {row['expected_speaker']}，"
                     f"原句「{row['quote']}」，实际说话人 {row['actual_speaker'] or '未匹配'}。")
    prefix.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
