"""Versioned creative preferences; they never replace output protocols or story rules."""

import json
from uuid import uuid4

from .creative_models import CreativeBrief
from .plot_store import plot_connection, save_record


PRESETS = [
    CreativeBrief(),
    CreativeBrief(
        template_id="suspense",
        name="悬疑追索",
        tone="克制、紧张、信息逐步揭示",
        pacing="balanced",
        instructions="用可验证的线索、人物选择和信息差推进悬疑。保留线索来源，不提前泄露谜底。",
    ),
    CreativeBrief(
        template_id="comedy",
        name="人物喜剧",
        tone="轻快、自然、人物反差",
        fidelity="balanced",
        instructions="从人物动机和处境错位制造幽默，避免凭空增加关键事件或改变说话人归属。",
    ),
    CreativeBrief(
        template_id="short_drama",
        name="短剧钩子",
        tone="直接、鲜明、冲突集中",
        pacing="fast",
        fidelity="balanced",
        instructions="减少重复说明，每场有明确目标、阻力和收束钩子；不牺牲因果或关键对白。",
    ),
    CreativeBrief(
        template_id="intimate",
        name="情感留白",
        tone="细腻、克制、富有潜台词",
        pacing="slow",
        instructions="通过停顿、动作和语气表现情绪变化，减少解释性独白，保留事件事实。",
    ),
    CreativeBrief(
        template_id="stage",
        name="舞台对峙",
        tone="对白清晰、空间集中",
        fidelity="balanced",
        instructions="突出站位、进退、沉默与对峙；给出可实施的调度，不把心理叙述直接搬入舞台。",
    ),
]


def list_templates():
    with plot_connection() as conn:
        rows = conn.execute(
            "SELECT payload FROM plot_records WHERE kind='creative_template' ORDER BY id"
        ).fetchall()
    custom = [CreativeBrief.model_validate_json(row[0]) for row in rows]
    return PRESETS + custom


def save_template(brief):
    # Saving creates an immutable version. Existing conversions retain their snapshot.
    existing = [b for b in list_templates() if b.template_id == brief.template_id]
    if brief.template_id.startswith("user-") and existing:
        version = max(b.template_version for b in existing) + 1
        template_id = brief.template_id
    else:
        version, template_id = 1, "user-" + str(uuid4())
    saved = brief.model_copy(update={"template_id": template_id, "template_version": version})
    save_record("creative_template", f"{template_id}:{version:06d}", saved.model_dump(mode="json"))
    return saved


def brief_prompt(brief):
    if brief is None:
        return ""
    return (
        "\n以下是作者的创作偏好数据，只作用于表达、节奏和风格；不能覆盖 JSON Schema、"
        "角色归属、既有事实、事件前提或版本约束。creative 保真档允许表达扩展，"
        "重大剧情变更仍需剧情改写流程。\n创作偏好："
        + json.dumps(brief.model_dump(mode="json"), ensure_ascii=False)
        + "\n"
    )


def configure_converter(converter, brief):
    if brief is not None:
        converter.creative_brief = brief.model_copy(deep=True)


def finish_conversion(screenplay, brief, mode):
    if brief is not None:
        screenplay.creative_brief = brief.model_copy(deep=True)
    return screenplay
