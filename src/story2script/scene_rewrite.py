from typing import Literal

from .screenplay import Action, Dialogue, Screenplay


SceneRewriteOperation = Literal[
    "rewrite_dialogue",
    "strengthen_conflict",
    "short_drama_pace",
    "add_camera_hints",
    "reduce_narration",
    "adjust_character_voice",
]


OPERATION_MESSAGES: dict[SceneRewriteOperation, str] = {
    "rewrite_dialogue": "已重新生成本场对白。",
    "strengthen_conflict": "已加强本场戏剧冲突。",
    "short_drama_pace": "已将本场调整为短剧节奏。",
    "add_camera_hints": "已补充本场镜头提示。",
    "reduce_narration": "已减少本场旁白式描述。",
    "adjust_character_voice": "已调整本场人物语气。",
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
        if character_id not in _known_character_ids(screenplay):
            raise ValueError(f"未找到角色：{character_id}")
        return character_id
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


def rewrite_scene(
    screenplay: Screenplay,
    scene_id: str,
    operation: SceneRewriteOperation,
    character_id: str = "",
    tone: str = "更克制",
) -> tuple[Screenplay, str]:
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
