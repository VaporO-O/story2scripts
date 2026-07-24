import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Protocol

from pydantic import BaseModel

from .llm_client import LLMClient, _default_env_path, _load_env_file, loads_json_object
from .scene_rewrite import OPERATION_PROMPTS, rewrite_scene
from .screenplay import Dialogue, Scene, Screenplay

REVIEW_CRITERIA = (
    "dramatization",
    "dialogue_conflict",
    "residual_narration",
    "character_voice",
)

CRITERIA_LABELS: dict[str, str] = {
    "dramatization": "戏剧化程度",
    "dialogue_conflict": "对白推动冲突",
    "residual_narration": "残留旁白",
    "character_voice": "人物语气一致性",
}

# 最低分项 -> 建议的局部重写操作
CRITERIA_OPERATIONS: dict[str, str] = {
    "dramatization": "rewrite_dialogue",
    "dialogue_conflict": "strengthen_conflict",
    "residual_narration": "reduce_narration",
    "character_voice": "adjust_character_voice",
}

DEFAULT_REVIEW_THRESHOLD = 7.0
DEFAULT_REVIEW_MAX_ROUNDS = 2

REVIEW_PROMPT_MARKER = "请对以下场景进行审校评分"


class SceneReviewResult(BaseModel):
    scene_id: str
    scores: dict[str, float]
    total: float
    verdict: Literal["pass", "fail"]
    issues: list[str] = []
    suggested_operation: str = ""
    feedback: str = ""
    round: int = 1


class HumanVerdict(BaseModel):
    scene_id: str
    status: Literal["approved", "rejected", "pending"] = "pending"
    comment: str = ""
    reviewed_at: str = ""


class ReviewReport(BaseModel):
    version: str = "1"
    screenplay_title: str = ""
    mode: str = "demo"
    threshold: float = DEFAULT_REVIEW_THRESHOLD
    rounds_used: int = 1
    machine: dict[str, SceneReviewResult] = {}
    human: dict[str, HumanVerdict] = {}
    summary: dict = {}


class SceneReviewer(Protocol):
    mode: str

    def review_scene(
        self, screenplay: Screenplay, scene: Scene, threshold: float
    ) -> SceneReviewResult: ...


def _clamp_score(value: object, default: float = 5.0) -> float:
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(10.0, score))


def _finalize_result(
    scene_id: str,
    scores: dict[str, float],
    issues: list[str],
    threshold: float,
    verdict: str = "",
    suggested_operation: str = "",
    feedback: str = "",
) -> SceneReviewResult:
    total = round(sum(scores.values()) / len(scores), 2) if scores else 0.0
    if verdict not in ("pass", "fail"):
        verdict = "pass" if total >= threshold else "fail"
    lowest = min(scores, key=lambda key: scores[key]) if scores else "dramatization"
    if suggested_operation not in OPERATION_PROMPTS:
        suggested_operation = CRITERIA_OPERATIONS.get(lowest, "reduce_narration")
    if not feedback:
        feedback = "；".join(issues)
    return SceneReviewResult(
        scene_id=scene_id,
        scores=scores,
        total=total,
        verdict=verdict,  # type: ignore[arg-type]
        issues=issues,
        suggested_operation=suggested_operation if verdict == "fail" else suggested_operation,
        feedback=feedback,
    )


class DemoSceneReviewer:
    """本地启发式场景审校器（默认，无需 API Key，结果确定可复现）。"""

    mode = "demo"

    def review_scene(
        self, screenplay: Screenplay, scene: Scene, threshold: float
    ) -> SceneReviewResult:
        dialogues = [element for element in scene.elements if isinstance(element, Dialogue)]
        actions = [element for element in scene.elements if element.type == "action"]
        issues: list[str] = []

        # 残留旁白：过长的动作段落被视为旁白式解释。
        long_actions = sum(1 for action in actions if len(action.text) > 60)
        residual = _clamp_score(10.0 - 3.0 * long_actions)
        if long_actions:
            issues.append(f"存在 {long_actions} 段偏长的叙述性动作描述，建议改写为可见动作或对白")

        # 对白推动冲突：需要有对白，且对白或冲突描述里有对抗性信号。
        conflict_markers = ("？", "！", "不", "别", "冲突", "必须", "凭什么")
        has_conflict_signal = any(
            marker in dialogue.text for dialogue in dialogues for marker in conflict_markers
        ) or scene.conflict.startswith(("冲突升级", "短剧冲突"))
        if not dialogues:
            dialogue_conflict = 3.0
            issues.append("本场没有对白，信息全靠叙述承载")
        else:
            dialogue_conflict = _clamp_score(6.0 + min(len(dialogues), 2) + (2.0 if has_conflict_signal else 0.0))
            if not has_conflict_signal:
                issues.append("对白缺少对抗性，未明显推动冲突")

        # 人物语气一致性：说话人必须在场，且语气标注（parenthetical/emotion）齐全。
        if not dialogues:
            character_voice = 6.0
        else:
            in_scene = sum(1 for dialogue in dialogues if dialogue.character in scene.characters)
            toned = sum(1 for dialogue in dialogues if dialogue.parenthetical or dialogue.emotion)
            character_voice = _clamp_score(8.0 * in_scene / len(dialogues) + 2.0 * toned / len(dialogues))
            if in_scene < len(dialogues):
                issues.append("存在说话人不在本场 characters 列表中的对白")

        # 戏剧化程度：动作与对白混合呈现、有镜头提示者更高。
        dramatization = 5.0
        if dialogues and actions:
            dramatization = 8.0
        elif dialogues or actions:
            dramatization = 6.0
        if scene.camera_hints:
            dramatization += 1.0
        if len(scene.dramatization_decisions) >= 2:
            dramatization += 1.0
        dramatization = _clamp_score(dramatization)
        if dramatization < 7.0:
            issues.append("场景元素单一，建议动作与对白混合呈现")

        scores = {
            "dramatization": dramatization,
            "dialogue_conflict": _clamp_score(dialogue_conflict),
            "residual_narration": residual,
            "character_voice": _clamp_score(character_voice),
        }
        return _finalize_result(scene.id, scores, issues, threshold)


class AISceneReviewer:
    """OpenAI-compatible 场景审校器：LLM 按四项标准打分并给出修正意见。"""

    mode = "ai"

    def __init__(self, llm_client: LLMClient | None = None, client=None) -> None:
        self.llm_client = llm_client or LLMClient(client=client, usage_label="AI scene review")

    def review_scene(
        self, screenplay: Screenplay, scene: Scene, threshold: float
    ) -> SceneReviewResult:
        prompt = self._build_prompt(screenplay, scene, threshold)
        content = self.llm_client.complete_json(prompt)
        try:
            payload = loads_json_object(content)
        except ValueError as exc:
            raise ValueError(f"AI 审校失败：模型返回的内容不是合法 JSON。{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("AI 审校失败：模型返回的内容不是评分对象。")

        raw_scores = payload.get("scores")
        raw_scores = raw_scores if isinstance(raw_scores, dict) else {}
        scores = {criterion: _clamp_score(raw_scores.get(criterion)) for criterion in REVIEW_CRITERIA}
        raw_issues = payload.get("issues")
        issues = [
            item.strip()
            for item in (raw_issues if isinstance(raw_issues, list) else [])
            if isinstance(item, str) and item.strip()
        ]
        verdict = payload.get("verdict") if isinstance(payload.get("verdict"), str) else ""
        suggested = payload.get("suggested_operation")
        feedback = payload.get("feedback") if isinstance(payload.get("feedback"), str) else ""
        return _finalize_result(
            scene.id,
            scores,
            issues,
            threshold,
            verdict=str(verdict),
            suggested_operation=str(suggested) if isinstance(suggested, str) else "",
            feedback=feedback.strip(),
        )

    def _build_prompt(self, screenplay: Screenplay, scene: Scene, threshold: float) -> str:
        context = {
            "screenplay": {
                "title": screenplay.title,
                "genre": screenplay.genre,
                "adaptation_type": screenplay.adaptation_type,
                "logline": screenplay.logline,
                "characters": [
                    {"id": character.id, "name": character.name}
                    for character in screenplay.characters
                ],
            },
            "scene": scene.model_dump(mode="json"),
            "criteria": CRITERIA_LABELS,
            "threshold": threshold,
            "allowed_operations": list(OPERATION_PROMPTS.keys()),
        }
        return (
            f"你是严格的剧本审校编辑。{REVIEW_PROMPT_MARKER}，四项标准各 0-10 分：\n"
            "- dramatization（戏剧化程度）：信息是否通过可见动作、对白与冲突呈现，而非概述；\n"
            "- dialogue_conflict（对白推动冲突）：对白是否推动目标与阻力的对抗；\n"
            "- residual_narration（残留旁白）：动作段落是否仍是解释性旁白（越少分越高）；\n"
            "- character_voice（人物语气一致性）：对白是否符合人物身份与既有语气。\n"
            "只返回可被 json.loads 解析的 JSON 对象，不要 Markdown，形如：\n"
            '{"scores": {"dramatization": 7, "dialogue_conflict": 6, "residual_narration": 8, '
            '"character_voice": 7}, "issues": ["..."], "verdict": "pass 或 fail", '
            '"suggested_operation": "allowed_operations 之一", "feedback": "给重写者的一句修正意见"}\n'
            f"verdict 规则：四项平均分低于 {threshold} 判 fail，否则 pass。\n"
            "suggested_operation 必须从 allowed_operations 中选择最能修复主要问题的一项。\n"
            f"上下文 JSON：{json.dumps(context, ensure_ascii=False)}"
        )


def get_scene_reviewer(mode: str = "demo", client=None) -> SceneReviewer:
    if mode == "demo":
        return DemoSceneReviewer()
    if mode == "ai":
        return AISceneReviewer(client=client)
    raise ValueError(f"不支持的审校模式：{mode}")


def _review_config(name: str, default: str) -> str:
    env_value = os.getenv(name)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    file_value = _load_env_file(_default_env_path()).get(name, "").strip()
    return file_value or default


def _review_threshold() -> float:
    raw = _review_config("AI_REVIEW_THRESHOLD", str(DEFAULT_REVIEW_THRESHOLD))
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("AI_REVIEW_THRESHOLD 必须是数字。") from exc


def _review_max_rounds() -> int:
    raw = _review_config("AI_REVIEW_MAX_ROUNDS", str(DEFAULT_REVIEW_MAX_ROUNDS))
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise ValueError("AI_REVIEW_MAX_ROUNDS 必须是整数。") from exc


def review_screenplay(
    screenplay: Screenplay,
    reviewer: SceneReviewer,
    threshold: float,
    scene_ids: list[str] | None = None,
    round_no: int = 1,
) -> dict[str, SceneReviewResult]:
    """按 scene_id 审校场景；AI 模式下并发执行，结果按剧本顺序收集。"""
    wanted = set(scene_ids) if scene_ids else None
    scenes = [scene for scene in screenplay.scenes if wanted is None or scene.id in wanted]
    if wanted is not None:
        missing = wanted - {scene.id for scene in scenes}
        if missing:
            raise ValueError(f"未找到场景：{'、'.join(sorted(missing))}")

    results: dict[str, SceneReviewResult] = {}
    if reviewer.mode == "ai" and len(scenes) > 1:
        max_workers = getattr(getattr(reviewer, "llm_client", None), "max_concurrency", 4)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(scenes)))) as executor:
            futures = [
                executor.submit(reviewer.review_scene, screenplay, scene, threshold)
                for scene in scenes
            ]
            for scene, future in zip(scenes, futures):
                result = future.result()
                result.round = round_no
                results[scene.id] = result
    else:
        for scene in scenes:
            result = reviewer.review_scene(screenplay, scene, threshold)
            result.round = round_no
            results[scene.id] = result
    return results


def _report_summary(results: dict[str, SceneReviewResult]) -> dict:
    totals = [result.total for result in results.values()]
    pass_count = sum(1 for result in results.values() if result.verdict == "pass")
    return {
        "scene_count": len(results),
        "avg_score": round(sum(totals) / len(totals), 2) if totals else 0.0,
        "pass_count": pass_count,
        "fail_count": len(results) - pass_count,
    }


def build_report(
    screenplay: Screenplay,
    results: dict[str, SceneReviewResult],
    mode: str,
    threshold: float,
    rounds_used: int,
) -> ReviewReport:
    return ReviewReport(
        screenplay_title=screenplay.title,
        mode=mode,
        threshold=threshold,
        rounds_used=rounds_used,
        machine=results,
        summary=_report_summary(results),
    )


def review_scenes_report(
    screenplay: Screenplay,
    mode: str = "demo",
    client=None,
    threshold: float | None = None,
    scene_ids: list[str] | None = None,
) -> ReviewReport:
    """只审不改：对全部或指定场景打分并生成报告。"""
    resolved_threshold = _review_threshold() if threshold is None else float(threshold)
    reviewer = get_scene_reviewer(mode, client=client)
    results = review_screenplay(screenplay, reviewer, resolved_threshold, scene_ids=scene_ids)
    return build_report(screenplay, results, mode, resolved_threshold, rounds_used=1)


def review_and_improve(
    screenplay: Screenplay,
    mode: str = "demo",
    client=None,
    threshold: float | None = None,
    max_rounds: int | None = None,
    auto_fix: bool = True,
    progress_cb=None,
) -> tuple[Screenplay, ReviewReport]:
    """审校全部场景；auto_fix 时对不达标场景带审校意见重写并复评。

    单个场景重写失败不会中断整体流程：保留原场景，并在该场景的
    issues 中记录失败原因，verdict 维持 fail。
    """
    resolved_threshold = _review_threshold() if threshold is None else float(threshold)
    resolved_rounds = _review_max_rounds() if max_rounds is None else max(1, int(max_rounds))
    reviewer = get_scene_reviewer(mode, client=client)

    if progress_cb:
        progress_cb(f"第 1 轮审校：正在评估 {len(screenplay.scenes)} 个场景。")
    results = review_screenplay(screenplay, reviewer, resolved_threshold, round_no=1)
    rounds_used = 1

    while auto_fix and rounds_used < resolved_rounds:
        failing = [
            scene.id
            for scene in screenplay.scenes
            if results[scene.id].verdict == "fail"
        ]
        if not failing:
            break

        rewritten: list[str] = []
        for scene_id in failing:
            result = results[scene_id]
            operation = result.suggested_operation or "reduce_narration"
            try:
                screenplay, _message = rewrite_scene(
                    screenplay=screenplay,
                    scene_id=scene_id,
                    operation=operation,  # type: ignore[arg-type]
                    mode=mode,  # type: ignore[arg-type]
                    client=client,
                    feedback=result.feedback,
                )
                rewritten.append(scene_id)
            except Exception as exc:  # noqa: BLE001 - 单场景失败不阻断整体审校
                result.issues.append(f"自动修正失败：{exc}")

        if not rewritten:
            break

        rounds_used += 1
        if progress_cb:
            progress_cb(f"第 {rounds_used} 轮审校：已重写 {len(rewritten)} 个场景，正在复评。")
        results.update(
            review_screenplay(
                screenplay, reviewer, resolved_threshold, scene_ids=rewritten, round_no=rounds_used
            )
        )

    report = build_report(screenplay, results, mode, resolved_threshold, rounds_used)
    return screenplay, report


def merge_human_verdicts(report: ReviewReport, verdicts: list[HumanVerdict]) -> ReviewReport:
    """把人审结论合并进审校报告；scene_id 必须已存在于机审结果中。"""
    merged = report.model_copy(deep=True)
    known_ids = set(merged.machine.keys())
    for verdict in verdicts:
        if known_ids and verdict.scene_id not in known_ids:
            raise ValueError(f"人审结论指向未知场景：{verdict.scene_id}")
        merged.human[verdict.scene_id] = verdict
    approved = sum(1 for item in merged.human.values() if item.status == "approved")
    rejected = sum(1 for item in merged.human.values() if item.status == "rejected")
    merged.summary = {
        **merged.summary,
        "human_approved": approved,
        "human_rejected": rejected,
        "human_pending": max(0, len(merged.machine) - approved - rejected),
    }
    return merged
