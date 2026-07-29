import json
import re
import time
from typing import Literal

from .converter import _normalize_screenplay_scene_data
from .llm_client import LLMClient, loads_json_object
from .metrics import metrics
from .screenplay import Action, Dialogue, Scene, Screenplay


SceneRewriteOperation = Literal[
    "rewrite_dialogue",
    "strengthen_conflict",
    "short_drama_pace",
    "add_camera_hints",
    "reduce_narration",
    "adjust_character_voice",
]
SceneRewriteMode = Literal["demo", "ai"]


OPERATION_MESSAGES: dict[SceneRewriteOperation, str] = {
    "rewrite_dialogue": "已重新生成本场对白。",
    "strengthen_conflict": "已加强本场戏剧冲突。",
    "short_drama_pace": "已将本场调整为短剧节奏。",
    "add_camera_hints": "已补充本场镜头提示。",
    "reduce_narration": "已减少本场旁白式描述。",
    "adjust_character_voice": "已调整本场人物语气。",
}

OPERATION_PROMPTS: dict[SceneRewriteOperation, str] = {
    "rewrite_dialogue": "只重新生成本场对白，让对白更能推动目标、冲突和人物关系。",
    "strengthen_conflict": "加强本场戏剧冲突，让阻力更明确，但不要改变场景来源。",
    "short_drama_pace": "把本场调整为短剧节奏，提高钩子、反转和冲突密度。",
    "add_camera_hints": "补充镜头提示，强调画面、景别和人物反应。",
    "reduce_narration": "减少旁白式解释，把信息改写为可见动作、对白或镜头提示。",
    "adjust_character_voice": "只调整指定人物的语气，保持场景结构和剧情事实稳定。",
}


def _find_scene(screenplay: Screenplay, scene_id: str):
    for scene in screenplay.scenes:
        if scene.id == scene_id:
            return scene
    raise ValueError(f"未找到场景：{scene_id}")


def _known_character_ids(screenplay: Screenplay) -> set[str]:
    return {character.id for character in screenplay.characters}


def _first_scene_character(screenplay: Screenplay, scene_id: str) -> str:
    scene = _find_scene(screenplay, scene_id)
    if scene.characters:
        return scene.characters[0]
    if screenplay.characters:
        return screenplay.characters[0].id
    return ""


def _resolve_character_id(screenplay: Screenplay, scene_id: str, character_id: str = "") -> str:
    if character_id:
        if character_id in _known_character_ids(screenplay):
            return character_id
        # 用户通常只知道角色名字，不知道 id：允许直接传入角色名并解析回稳定 id。
        for character in screenplay.characters:
            if character.name == character_id:
                return character.id
        raise ValueError(f"未找到角色：{character_id}")
    return _first_scene_character(screenplay, scene_id)


def _scene_dialogues(scene) -> list[Dialogue]:
    return [element for element in scene.elements if isinstance(element, Dialogue)]


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _prepend_action(scene, text: str) -> None:
    scene.elements.insert(0, Action(type="action", text=text))


def _first_sentence(text: str, limit: int = 90) -> str:
    for separator in ["。", "！", "？", ".", "!", "?"]:
        if separator in text:
            return text.split(separator, 1)[0][:limit].strip() + separator
    return text[:limit].strip()


def _rewrite_dialogue(screenplay: Screenplay, scene_id: str) -> None:
    scene = _find_scene(screenplay, scene_id)
    dialogues = _scene_dialogues(scene)
    if not dialogues:
        character_id = _resolve_character_id(screenplay, scene_id)
        if not character_id:
            _prepend_action(scene, "对白重写提示：本场需要先补充可说话角色。")
            return
        _append_unique(scene.characters, character_id)
        _append_unique(scene.characters_present, character_id)
        scene.elements.append(
            Dialogue(
                type="dialogue",
                character=character_id,
                parenthetical="更直接",
                emotion="推进",
                text="我们不能再绕开这个问题。",
            )
        )
        return

    for dialogue in dialogues:
        dialogue.parenthetical = dialogue.parenthetical or "更直接"
        dialogue.emotion = dialogue.emotion or "推进"
        dialogue.text = f"我们把话说清楚：{dialogue.text}"


def _strengthen_conflict(screenplay: Screenplay, scene_id: str) -> None:
    scene = _find_scene(screenplay, scene_id)
    scene.conflict = f"冲突升级：角色目标遇到更明确的阻力。{scene.conflict}"
    scene.beat = f"{scene.beat}、冲突升级"
    _prepend_action(scene, "冲突升级：一个新的阻力打断原有行动，角色必须立刻选择。")


def _short_drama_pace(screenplay: Screenplay, scene_id: str) -> None:
    scene = _find_scene(screenplay, scene_id)
    scene.conflict = f"短剧冲突：更快抛出阻力和反转。{scene.conflict}"
    scene.beat = f"短剧节奏：{scene.beat}"
    scene.subtext = f"短剧潜台词：每句对白都压向钩子和反转。{scene.subtext}"
    _prepend_action(scene, "短剧节奏：本场开头直接给出压力点，快速进入对抗。")
    _append_unique(scene.camera_hints, "节奏提示：结尾保留一个可继续追看的反转钩子。")


def _add_camera_hints(screenplay: Screenplay, scene_id: str) -> None:
    scene = _find_scene(screenplay, scene_id)
    for hint in [
        "镜头提示：先用全景建立人物与空间关系。",
        "镜头提示：冲突升级时切近景捕捉人物反应。",
        "镜头提示：场景收束前保留一个细节特写。",
    ]:
        _append_unique(scene.camera_hints, hint)


def _reduce_narration(screenplay: Screenplay, scene_id: str) -> None:
    scene = _find_scene(screenplay, scene_id)
    for element in scene.elements:
        if isinstance(element, Action):
            element.text = f"动作呈现：{_first_sentence(element.text)}"
    scene.subtext = f"减少旁白：保留可见行动，把解释性交给动作和对白。{scene.subtext}"


def _adjust_character_voice(
    screenplay: Screenplay,
    scene_id: str,
    character_id: str = "",
    tone: str = "更克制",
) -> None:
    scene = _find_scene(screenplay, scene_id)
    resolved_character_id = _resolve_character_id(screenplay, scene_id, character_id)
    if not resolved_character_id:
        _prepend_action(scene, "语气调整提示：本场需要先补充可调整语气的角色。")
        return

    matched = False
    for dialogue in _scene_dialogues(scene):
        if dialogue.character == resolved_character_id:
            dialogue.parenthetical = tone
            dialogue.emotion = tone
            matched = True

    if not matched:
        _append_unique(scene.characters, resolved_character_id)
        _append_unique(scene.characters_present, resolved_character_id)
        scene.elements.append(
            Dialogue(
                type="dialogue",
                character=resolved_character_id,
                parenthetical=tone,
                emotion=tone,
                text="我会换一种方式说清楚。",
            )
        )

    scene.subtext = f"人物语气：{tone}。{scene.subtext}"


def _backfill_scene_invariants(raw_scene: dict, target_scene: Scene) -> dict:
    """Fill invariants the model omitted with the target scene's values.

    A local rewrite must keep id / source_chapter / int_ext / time_of_day /
    location. Missing or malformed values are restored from the target so a
    minor omission does not fail the rewrite; values the model explicitly
    changed are left in place so the guards below can reject them.
    """
    if not re.fullmatch(r"scene-[0-9]+", str(raw_scene.get("id", "")).strip()):
        raw_scene["id"] = target_scene.id
    for key, value in (
        ("source_chapter", target_scene.source_chapter),
        ("int_ext", target_scene.int_ext),
        ("time_of_day", target_scene.time_of_day),
        ("location", target_scene.location),
    ):
        current = raw_scene.get(key)
        if not (isinstance(current, str) and current.strip()):
            raw_scene[key] = value
    return raw_scene


class AISceneRewriter:
    """OpenAI-compatible local scene rewriter.

    The LLM is only allowed to return a replacement Scene object. The full
    screenplay is validated again after replacement so bad references cannot
    leak into YAML output.
    """

    def __init__(self, llm_client: LLMClient | None = None, client=None) -> None:
        self.llm_client = llm_client or LLMClient(
            client=client,
            usage_label="AI scene rewrite",
        )

    def rewrite(
        self,
        screenplay: Screenplay,
        scene_id: str,
        operation: SceneRewriteOperation,
        character_id: str = "",
        tone: str = "更克制",
        feedback: str = "",
    ) -> Screenplay:
        target_scene = _find_scene(screenplay, scene_id)
        if character_id:
            _resolve_character_id(screenplay, scene_id, character_id)

        prompt = self._build_prompt(screenplay, target_scene, operation, character_id, tone, feedback)
        content = self.llm_client.complete_json(prompt)
        try:
            raw_scene = loads_json_object(content)
        except ValueError as exc:
            raise ValueError(f"AI 局部重写失败：模型返回的内容不是合法 JSON。{exc}") from exc
        if not isinstance(raw_scene, dict):
            raise ValueError("AI 局部重写失败：模型返回的内容不是 Scene 对象。")

        _backfill_scene_invariants(raw_scene, target_scene)
        known_ids = {character.id for character in screenplay.characters}
        name_to_id = {
            character.name: character.id
            for character in screenplay.characters
            if character.name
        }
        normalized = _normalize_screenplay_scene_data(
            [raw_scene],
            known_ids,
            name_to_id,
            list(screenplay.source.chapter_titles),
        )
        if not normalized:
            raise ValueError("AI 局部重写失败：模型没有返回有效场景。")
        replacement_scene = Scene.model_validate(normalized[0])

        if replacement_scene.id != target_scene.id:
            raise ValueError("AI scene rewrite must keep the original scene id.")
        if replacement_scene.source_chapter != target_scene.source_chapter:
            raise ValueError("AI scene rewrite must keep the original source_chapter.")
        if replacement_scene.int_ext != target_scene.int_ext:
            raise ValueError("AI scene rewrite must keep the original int_ext.")
        if replacement_scene.time_of_day != target_scene.time_of_day:
            raise ValueError("AI scene rewrite must keep the original time_of_day.")
        if replacement_scene.location != target_scene.location:
            raise ValueError("AI scene rewrite must keep the original location.")

        updated = screenplay.model_copy(deep=True)
        for index, scene in enumerate(updated.scenes):
            if scene.id == scene_id:
                updated.scenes[index] = replacement_scene
                break

        return Screenplay.model_validate(updated.model_dump(mode="json"))

    def _build_prompt(
        self,
        screenplay: Screenplay,
        target_scene,
        operation: SceneRewriteOperation,
        character_id: str,
        tone: str,
        feedback: str = "",
    ) -> str:
        context = {
            "screenplay": {
                "title": screenplay.title,
                "genre": screenplay.genre,
                "adaptation_type": screenplay.adaptation_type,
                "logline": screenplay.logline,
                "global_state": screenplay.global_state.model_dump(mode="json"),
                "characters": [character.model_dump(mode="json") for character in screenplay.characters],
            },
            "target_scene": target_scene.model_dump(mode="json"),
            "operation": operation,
            "operation_goal": OPERATION_PROMPTS[operation],
            "character_id": character_id,
            "tone": tone,
        }
        return (
            "你是专业影视编剧。请基于给定剧本上下文，只局部重写 target_scene。\n"
            "只返回一个符合 Story2Script Scene Schema 的 JSON 对象，不要 Markdown，不要返回完整剧本。\n"
            "硬性要求：id 必须保持不变；source_chapter 必须保持不变；"
            "int_ext、time_of_day、location 必须保持不变；"
            "characters 和 dialogue.character 只能引用已存在角色 id；"
            "必须遵守 global_state 中的人物表、地点表和时间线，不要改写跨章节事实；"
            "必须保留 heading, int_ext, time_of_day, location, characters_present, props, "
            "dramatization_decisions, summary, goal, conflict, beat, subtext, characters, "
            "elements, camera_hints。\n"
            "本次局部操作："
            f"{OPERATION_PROMPTS[operation]}\n"
            + (f"审校意见（请重点修正以下问题）：{feedback}\n" if feedback else "")
            + f"上下文 JSON：{json.dumps(context, ensure_ascii=False)}"
        )


def _rewrite_scene_with_ai(
    screenplay: Screenplay,
    scene_id: str,
    operation: SceneRewriteOperation,
    character_id: str = "",
    tone: str = "更克制",
    client=None,
    feedback: str = "",
) -> Screenplay:
    return AISceneRewriter(client=client).rewrite(
        screenplay=screenplay,
        scene_id=scene_id,
        operation=operation,
        character_id=character_id,
        tone=tone,
        feedback=feedback,
    )


def rewrite_scene(
    screenplay: Screenplay,
    scene_id: str,
    operation: SceneRewriteOperation,
    character_id: str = "",
    tone: str = "更克制",
    mode: SceneRewriteMode = "demo",
    client=None,
    feedback: str = "",
) -> tuple[Screenplay, str]:
    started = time.perf_counter()
    try:
        result = _rewrite_scene_dispatch(
            screenplay=screenplay,
            scene_id=scene_id,
            operation=operation,
            character_id=character_id,
            tone=tone,
            mode=mode,
            client=client,
            feedback=feedback,
        )
    except ValueError as exc:
        metrics.record_task(
            "scene_rewrite",
            mode=mode,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=False,
            error=str(exc),
            extra={"operation": operation, "scene_id": scene_id},
        )
        raise
    metrics.record_task(
        "scene_rewrite",
        mode=mode,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=True,
        extra={"operation": operation, "scene_id": scene_id},
    )
    return result


def _rewrite_scene_dispatch(
    screenplay: Screenplay,
    scene_id: str,
    operation: SceneRewriteOperation,
    character_id: str = "",
    tone: str = "更克制",
    mode: SceneRewriteMode = "demo",
    client=None,
    feedback: str = "",
) -> tuple[Screenplay, str]:
    if mode == "ai":
        updated = _rewrite_scene_with_ai(
            screenplay=screenplay,
            scene_id=scene_id,
            operation=operation,
            character_id=character_id,
            tone=tone,
            client=client,
            feedback=feedback,
        )
        return updated, f"AI {OPERATION_MESSAGES[operation]}"
    if mode != "demo":
        raise ValueError(f"不支持的局部重写模式：{mode}")

    updated = screenplay.model_copy(deep=True)

    if operation == "rewrite_dialogue":
        _rewrite_dialogue(updated, scene_id)
    elif operation == "strengthen_conflict":
        _strengthen_conflict(updated, scene_id)
    elif operation == "short_drama_pace":
        _short_drama_pace(updated, scene_id)
    elif operation == "add_camera_hints":
        _add_camera_hints(updated, scene_id)
    elif operation == "reduce_narration":
        _reduce_narration(updated, scene_id)
    elif operation == "adjust_character_voice":
        _adjust_character_voice(updated, scene_id, character_id, tone)
    else:
        raise ValueError(f"不支持的局部重写操作：{operation}")

    return Screenplay.model_validate(updated.model_dump(mode="json")), OPERATION_MESSAGES[operation]
