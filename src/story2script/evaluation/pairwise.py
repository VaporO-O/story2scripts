"""Blind single-Agent versus multi-Agent review packets and scoring."""

from __future__ import annotations

import json
import random
from pathlib import Path

from .models import EvalReport

PAIRWISE_VARIANTS = ("single_agent", "multi_agent")
PAIRWISE_CRITERIA = [
    "剧情信息是否忠于原文",
    "场景是否可拍摄且动作明确",
    "对白是否推动目标与冲突",
    "人物语气与跨章状态是否一致",
    "整体成稿是否需要更少人工修改",
]


def build_blind_review(report: EvalReport, seed: int = 2025) -> tuple[dict, dict, dict]:
    if not all(name in report.summary.get("variants", {}) for name in PAIRWISE_VARIANTS):
        raise ValueError("生成盲测材料需要同时运行 single_agent 和 multi_agent。")

    rng = random.Random(seed)
    pairs: list[dict] = []
    keys: list[dict] = []
    responses: list[dict] = []
    for case in report.cases:
        single = case.variants.get("single_agent")
        team = case.variants.get("multi_agent")
        if single is not None and single.status == "failed":
            continue
        if team is not None and team.status == "failed":
            continue
        if single is None or team is None or single.screenplay is None or team.screenplay is None:
            raise ValueError(f"{case.case_id} 缺少可用于盲测的 Agent 输出。")

        variants = list(PAIRWISE_VARIANTS)
        rng.shuffle(variants)
        outputs = {
            "single_agent": single.screenplay.model_dump(mode="json"),
            "multi_agent": team.screenplay.model_dump(mode="json"),
        }
        pair_id = f"{case.case_id}-s{case.sample_index:02d}"
        pairs.append(
            {
                "pair_id": pair_id,
                "case_id": case.case_id,
                "sample_index": case.sample_index,
                "title": case.title,
                "source_text": case.source_text,
                "candidate_a": outputs[variants[0]],
                "candidate_b": outputs[variants[1]],
            }
        )
        keys.append(
            {
                "pair_id": pair_id,
                "candidate_a": variants[0],
                "candidate_b": variants[1],
            }
        )
        responses.append({"pair_id": pair_id, "preference": "", "reason": ""})

    if not pairs:
        raise ValueError("没有同时成功的单 Agent / 多 Agent 输出可用于盲测。")

    packet = {
        "version": "1",
        "generated_at": report.generated_at,
        "model": report.model,
        "instructions": (
            "在不知道候选方案来源的前提下，逐对选择 A、B 或 TIE。"
            "请依据给定标准判断哪份成稿更可用。"
        ),
        "criteria": PAIRWISE_CRITERIA,
        "pairs": pairs,
    }
    response_template = {
        "version": "1",
        "reviewer": "",
        "reviews": responses,
    }
    answer_key = {"version": "1", "seed": seed, "pairs": keys}
    return packet, response_template, answer_key


def write_blind_review_files(
    report: EvalReport,
    output_dir: str | Path,
    prefix: str,
    seed: int = 2025,
) -> tuple[Path, Path, Path]:
    packet, responses, key = build_blind_review(report, seed=seed)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = (
        target / f"{prefix}-blind-review.json",
        target / f"{prefix}-blind-responses.json",
        target / f"{prefix}-blind-key.json",
    )
    for path, payload in zip(paths, (packet, responses, key), strict=True):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return paths


def score_blind_reviews(responses: dict, answer_key: dict) -> dict:
    mapping = {item["pair_id"]: item for item in answer_key.get("pairs", [])}
    wins = {"single_agent": 0, "multi_agent": 0, "tie": 0}
    reviewed = 0
    pending = 0
    errors: list[str] = []
    seen: set[str] = set()
    for row in responses.get("reviews", []):
        pair_id = str(row.get("pair_id", ""))
        if pair_id in seen:
            errors.append(f"重复 pair_id：{pair_id}")
            continue
        seen.add(pair_id)
        key = mapping.get(pair_id)
        if key is None:
            errors.append(f"未知 pair_id：{pair_id}")
            continue
        preference = str(row.get("preference", "")).strip().upper()
        if not preference:
            pending += 1
            continue
        if preference == "TIE":
            wins["tie"] += 1
        elif preference in {"A", "B"}:
            wins[key[f"candidate_{preference.lower()}"]] += 1
        else:
            errors.append(f"{pair_id} 的 preference 必须是 A、B 或 TIE。")
            continue
        reviewed += 1

    decided = wins["single_agent"] + wins["multi_agent"]
    return {
        "version": "1",
        "reviewer": responses.get("reviewer", ""),
        "reviewed": reviewed,
        "pending": pending,
        "errors": errors,
        "wins": wins,
        "single_agent_preference_rate": (
            round(wins["single_agent"] / decided, 4) if decided else None
        ),
        "multi_agent_preference_rate": (
            round(wins["multi_agent"] / decided, 4) if decided else None
        ),
    }


def score_blind_review_files(
    responses_path: str | Path,
    key_path: str | Path,
    output_path: str | Path,
) -> Path:
    responses = json.loads(Path(responses_path).read_text(encoding="utf-8"))
    answer_key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    summary = score_blind_reviews(responses, answer_key)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
