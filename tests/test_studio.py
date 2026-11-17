import json
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient

from story2script.creative_models import CreativeBrief, EditingState, SceneGroup
from story2script.creative_templates import brief_prompt, list_templates, save_template
from story2script.main import app
from story2script.plot_state import screenplay_hash
from story2script.plot_store import load_record
from story2script.screenplay import Dialogue, Screenplay
from story2script.screenplay_export import script_docx, script_html, script_txt
from story2script.studio import annotated_emotions, apply_edit, balanced_groups, projection
from story2script.studio_api import emotion_analysis, propose_structure, recheck_script
from story2script.studio_models import (
    DraftRequest,
    EmotionRequest,
    RecheckRequest,
    StructureRequest,
)
from story2script.studio_store import DraftConflict, draft_versions, save_draft
from story2script.yaml_export import screenplay_to_yaml
from test_plot_revision import completed_revision, plot_script


def dialogue_script():
    s = plot_script()
    for i, scene in enumerate(s.scenes):
        scene.elements.append(
            Dialogue(
                type="dialogue",
                character="character-1" if i % 2 == 0 else "character-2",
                parenthetical="低声",
                emotion="疑惑" if i == 0 else "",
                text=f"第{i + 1}场：钥匙究竟在哪里？",
            )
        )
    return s


@pytest.mark.parametrize("kind", ["act", "episode"])
def test_groups_preserve_all_scenes_and_target_duration(kind):
    s = plot_script()
    groups = balanced_groups(s, kind, 3, 12)
    assert [sid for g in groups for sid in g.scene_ids] == [v.id for v in s.scenes]
    updated = apply_edit(s, structure=groups)
    assert updated.scenes == s.scenes and updated.structure[0].target_minutes == 12
    assert s.structure == []


@pytest.mark.parametrize(
    "ids", [["scene-2", "scene-1", "scene-3", "scene-4"], ["scene-1"], ["scene-1"] * 4]
)
def test_structure_cannot_reorder_omit_or_duplicate(ids):
    with pytest.raises(ValueError, match="完整覆盖"):
        apply_edit(
            plot_script(),
            structure=[SceneGroup(id="act-1", kind="act", title="开始", scene_ids=ids)],
        )


def test_act_and_episode_partitions_can_coexist():
    s = plot_script()
    result = apply_edit(
        s, structure=balanced_groups(s, "act", 3) + balanced_groups(s, "episode", 2)
    )
    assert len(result.structure) == 5
    with pytest.raises(ValueError, match="不能超过"):
        balanced_groups(s, "episode", 5)


class JsonModel:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete_json(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return json.dumps(self.response, ensure_ascii=False)


def test_ai_structure_is_validated_before_apply():
    s = plot_script()
    groups = balanced_groups(s, "act", 2)
    stub = JsonModel({"groups": [g.model_dump() for g in groups]})
    req = StructureRequest(yaml_text=screenplay_to_yaml(s), kind="act", count=2, mode="ai")
    assert propose_structure(req, client=stub).scenes == s.scenes
    stub.response["groups"][0]["scene_ids"].reverse()
    with pytest.raises(ValueError, match="完整覆盖"):
        propose_structure(req, client=stub)


def test_edit_marks_logic_and_reviews_stale_without_discarding_history():
    _, revision, _ = completed_revision()
    base = revision.screenplay
    scene = base.scenes[-1].model_copy(deep=True)
    scene.elements[0].text = "周宁改走另一条路线。"
    changed = apply_edit(base, scene=scene)
    assert changed.editing.status == "needs_check"
    assert changed.editing.dirty_scene_ids == [scene.id]
    assert changed.plot_state.logic == base.plot_state.logic
    assert not projection(changed)["authoritative"]
    assert "草稿" in script_txt(changed)
    with pytest.raises(ValueError, match="事件时间"):
        scene.timeline_mode = "flashback"
        apply_edit(base, scene=scene)


def test_projection_uses_selected_boundary_and_links_real_producers():
    _, r, _ = completed_revision()
    before = projection(r.screenplay, "scene-2", "start")
    after = projection(r.screenplay, "scene-2", "end")

    def alive(p):
        return next(
            f["value"]
            for c in p["characters"]
            if c["id"] == "character-1"
            for f in c["facts"]
            if f["predicate"] == "alive"
        )

    assert alive(before) == "true" and alive(after) == "false"
    assert before["authoritative"] and after["authoritative"]
    assert all(e["scene_id"] in ["scene-1", "scene-2"] for e in after["events"])
    death = next(e for e in after["events"] if e["rule"] == "death")
    assert after["sources"][death["event_id"]]["evidence"]


def test_without_ledger_relations_are_only_cooccurrence():
    result = projection(plot_script())
    assert not result["authoritative"]
    assert result["relationships"] and all(
        r["kind"] == "cooccurrence" for r in result["relationships"]
    )
    assert all(not c["facts"] for c in result["characters"])


def test_relation_graph_has_no_future_relationship_leak():
    _, r, _ = completed_revision(
        operation="relationship",
        target="character-2",
        relationship="enemy",
        scene_id="scene-4",
        instruction="变为敌对",
    )
    before = projection(r.screenplay, "scene-4", "start")
    after = projection(r.screenplay, "scene-4", "end")
    assert not any(rel["kind"] == "fact" for rel in before["relationships"])
    rel = next(rel for rel in after["relationships"] if rel["kind"] == "fact")
    assert rel["label"] == "enemy" and rel["source"] in after["sources"]


def test_draft_compare_and_swap_history_and_durable_reload():
    base = plot_script()
    first = save_draft(DraftRequest(screenplay=base))
    changed = base.model_copy(deep=True)
    changed.scenes[0].elements[0].text = "新的动作。"
    second = save_draft(
        DraftRequest(screenplay=changed, draft_id=first["draft_id"], expected_version=1)
    )
    assert second["version"] == 2 and second["screenplay"]["editing"]["status"] == "needs_check"
    assert (
        load_record("studio_snapshot", f"{first['draft_id']}:00000001")["screenplay"]
        == first["screenplay"]
    )
    with pytest.raises(DraftConflict):
        save_draft(DraftRequest(screenplay=base, draft_id=first["draft_id"], expected_version=1))
    assert len(draft_versions(first["draft_id"])) == 2


def test_client_cannot_clear_dirty_flag_by_saving_again():
    base = plot_script()
    first = save_draft(DraftRequest(screenplay=base))
    base.scenes[0].summary = "新摘要"
    second = save_draft(
        DraftRequest(screenplay=base, draft_id=first["draft_id"], expected_version=1)
    )
    forged = Screenplay.model_validate(second["screenplay"])
    forged.editing = None
    saved = save_draft(
        DraftRequest(screenplay=forged, draft_id=first["draft_id"], expected_version=2)
    )
    assert saved["screenplay"]["editing"]["status"] == "needs_check"


def test_draft_api_history_conflicts_and_missing():
    c = TestClient(app)
    created = c.post(
        "/api/studio/drafts", json={"screenplay": plot_script().model_dump(mode="json")}
    ).json()
    assert c.get(f"/api/studio/drafts/{created['draft_id']}?version=1").json()["version"] == 1
    assert c.get("/api/studio/drafts/missing").status_code == 404
    assert (
        c.post(
            "/api/studio/drafts",
            json={
                "screenplay": plot_script().model_dump(mode="json"),
                "draft_id": created["draft_id"],
                "expected_version": 0,
            },
        ).status_code
        == 409
    )


def test_templates_are_versioned_immutable_snapshots():
    first = save_template(CreativeBrief(name="夜色悬疑", instructions="少解释，多行动。"))
    second = save_template(first.model_copy(update={"tone": "更加紧张"}))
    assert first.template_id == second.template_id and second.template_version == 2
    old = next(
        v
        for v in list_templates()
        if v.template_id == first.template_id and v.template_version == 1
    )
    assert old == first and old.tone != second.tone
    assert "不能覆盖 JSON Schema" in brief_prompt(second)


def test_brief_only_changes_preferences_and_survives_export():
    s = plot_script()
    brief = CreativeBrief(template_id="comedy", name="喜剧", tone="轻快")
    changed = apply_edit(s, creative_brief=brief)
    assert changed.scenes == s.scenes and changed.creative_brief == brief
    assert "喜剧" in script_txt(changed)


def test_sync_conversion_keeps_brief_snapshot_and_configures_model(monkeypatch):
    import story2script.main as module
    from story2script.converter import DemoConverter

    converter = DemoConverter()
    monkeypatch.setattr(module, "get_converter", lambda mode: converter)
    response = TestClient(app).post(
        "/api/convert",
        json={
            "novel_text": "第一章\n林夏说：你好。\n第二章\n周宁走进车站。\n第三章\n林夏回家。",
            "creative_brief": {"name": "悬疑", "tone": "紧张"},
        },
    )
    assert response.status_code == 200
    assert response.json()["screenplay"]["creative_brief"]["tone"] == "紧张"
    assert converter.creative_brief.tone == "紧张"


def test_emotion_notes_preserve_unknown_and_exact_speakers():
    s = dialogue_script()
    notes = annotated_emotions(s)
    assert notes[0]["emotion"] == "疑惑"
    assert notes[1]["emotion"] == "未标注" and notes[1]["source"] == "unknown"
    assert notes[1]["character_id"] == "character-2"


@pytest.mark.parametrize("bad", ["speaker", "quote", "missing", "duplicate"])
def test_ai_emotions_require_grounding_and_full_coverage(bad):
    s = dialogue_script()
    notes = annotated_emotions(s)
    rows = [{k: v for k, v in n.items() if k != "source"} for n in notes]
    if bad == "speaker":
        rows[0]["character_id"] = "character-2"
    elif bad == "quote":
        rows[0]["quote"] = "不存在的台词"
    elif bad == "missing":
        rows.pop()
    else:
        rows[-1] = rows[0]
    with pytest.raises(ValueError):
        emotion_analysis(
            EmotionRequest(yaml_text=screenplay_to_yaml(s), mode="ai"),
            client=JsonModel({"notes": rows}),
        )


def test_grounded_emotion_analysis_never_changes_prose():
    s = dialogue_script()
    before = s.model_dump_json()
    notes = [{k: v for k, v in n.items() if k != "source"} for n in annotated_emotions(s)]
    notes[0]["emotion"] = "怀疑"
    result = emotion_analysis(
        EmotionRequest(yaml_text=screenplay_to_yaml(s), mode="ai"),
        client=JsonModel({"notes": notes}),
    )
    assert result["notes"][0]["source"] == "ai" and s.model_dump_json() == before


def test_export_preserves_dialogue_order_chinese_and_escapes_html():
    s = dialogue_script()
    s.title = "中文 <script>alert(1)</script> & 测试"
    s = apply_edit(s, structure=balanced_groups(s, "act", 2))
    txt = script_txt(s)
    html = script_html(s)
    assert txt.index("scene-1") < txt.index("scene-4")
    assert "第1场：钥匙究竟在哪里？" in txt
    assert "<script>" not in html and "&lt;script&gt;" in html
    doc = Document(BytesIO(script_docx(s)))
    paragraphs = "\n".join(p.text for p in doc.paragraphs)
    assert s.title in paragraphs and "第4场：钥匙究竟在哪里？" in paragraphs
    assert doc.sections[0].page_width.inches == 8.5
    assert len(doc.tables) == 1 and len(doc.tables[0].rows) == 5
    assert "tblHeader" in doc.tables[0]._tbl.xml


@pytest.mark.parametrize(
    "format,content_type",
    [("txt", "text/plain"), ("html", "text/html"), ("docx", "application/vnd.openxmlformats")],
)
def test_export_api_returns_requested_version_and_filename(format, content_type):
    s = plot_script()
    s.scenes[-1].elements[0].text = "当前查看版本专有文本"
    response = TestClient(app).post(
        "/api/studio/export",
        json={"yaml_text": screenplay_to_yaml(s), "format": format, "include_appendix": False},
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith(content_type)
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    if format != "docx":
        assert "当前查看版本专有文本" in response.text


def test_rule_only_check_cannot_clear_dirty_semantic_state():
    _, r, _ = completed_revision()
    s = r.screenplay
    s.editing = EditingState(based_on_hash=screenplay_hash(s), dirty_scene_ids=["scene-4"])
    result = recheck_script(RecheckRequest(yaml_text=screenplay_to_yaml(s), mode="rules"))
    assert result["valid"] is False and result["screenplay"].editing.status == "needs_check"


def test_ai_recheck_keeps_frozen_initial_facts_and_is_persistable():
    _, r, _ = completed_revision()
    s = r.screenplay
    s.editing = EditingState(based_on_hash=screenplay_hash(s), dirty_scene_ids=["scene-4"])
    first = save_draft(DraftRequest(screenplay=s))

    class Model:
        def complete_json(self, prompt, **kw):
            if kw["prompt_id"] == "studio.scene_recheck":
                return json.dumps(
                    {
                        "scene_id": "scene-4",
                        "issues": [],
                        "facts": [],
                        "logic": s.plot_state.logic.scenes[-1].model_dump(mode="json"),
                    }
                )
            return json.dumps({"scanned_scene_ids": [x.id for x in s.scenes], "issues": []})

    checked = recheck_script(
        RecheckRequest(yaml_text=screenplay_to_yaml(s), mode="ai"), client=Model()
    )
    assert (
        checked["valid"]
        and checked["screenplay"].plot_state.logic.initial_facts == s.plot_state.logic.initial_facts
    )
    saved = save_draft(
        DraftRequest(
            screenplay=checked["screenplay"], draft_id=first["draft_id"], expected_version=1
        )
    )
    assert saved["screenplay"]["editing"]["status"] == "checked"


def test_old_screenplay_hash_does_not_change_with_optional_authoring_defaults():
    import hashlib

    s = plot_script()
    data = s.model_dump(mode="json")
    for key in ("creative_brief", "structure", "editing"):
        data.pop(key)
    expected = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert screenplay_hash(s) == expected


def test_source_edit_validation_invalidates_previous_story_status():
    old = plot_script()
    edited = old.model_copy(deep=True)
    edited.scenes[0].summary = "用户直接编辑了源码摘要"
    response = TestClient(app).post(
        "/api/yaml/validate",
        json={"yaml_text": screenplay_to_yaml(edited), "previous_yaml": screenplay_to_yaml(old)},
    )
    assert response.status_code == 200
    assert response.json()["screenplay"]["editing"]["status"] == "needs_check"
    assert "needs_check" in response.json()["yaml_text"]
    checked_data = response.json()
    attempted_clear = Screenplay.model_validate(checked_data["screenplay"])
    attempted_clear.editing = None
    again = TestClient(app).post("/api/yaml/validate", json={"yaml_text": screenplay_to_yaml(attempted_clear),
                                                           "previous_yaml": checked_data["yaml_text"]})
    assert again.json()["screenplay"]["editing"]["status"] == "needs_check"


def test_failed_ai_audit_does_not_leave_checked_state():
    script = plot_script()
    script.editing = EditingState(status="checked", based_on_hash=screenplay_hash(script))
    model = JsonModel(
        {
            "scanned_scene_ids": [s.id for s in script.scenes],
            "issues": [{"scene_id": "scene-1", "kind": "causality", "message": "缺少信息来源"}],
        }
    )
    result = recheck_script(
        RecheckRequest(yaml_text=screenplay_to_yaml(script), mode="ai"), client=model
    )
    assert not result["valid"] and result["screenplay"].editing.status == "needs_check"
