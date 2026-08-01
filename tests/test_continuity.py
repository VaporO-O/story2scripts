import json

import httpx
import pytest

from story2script.continuity import (
    CONTINUITY_PROMPT_MARKER,
    check_continuity,
    summarize_findings,
)
from story2script.converter import DemoConverter
from story2script.examples import load_example_novel
from story2script.metrics import metrics
from story2script.parser import parse_chapters
from story2script.screenplay import GlobalLocationState

NOVEL = "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n林夏说：“等等。”\n第三章 结局\n太阳升起。"


def sample_screenplay():
    return DemoConverter().convert(parse_chapters(NOVEL), title="测试故事", genre="剧情")


def kinds(findings) -> list[str]:
    return [item.kind for item in findings]


# ---------------------------------------------------------------- 干净基线


def test_clean_screenplay_has_no_findings():
    assert check_continuity(sample_screenplay()) == []


def test_example_novel_has_no_false_positives():
    """45 场景的示例小说不应刷出噪音告警（地点占位符等属转换质量，不是跨章矛盾）。"""
    example = load_example_novel()
    screenplay = DemoConverter().convert(
        parse_chapters(example["novel_text"]), title=example["title"], genre=example["genre"]
    )

    findings = check_continuity(screenplay)

    assert findings == [], f"示例小说出现误报：{summarize_findings(findings)}"


# ---------------------------------------------------------------- 四类规则


def test_detects_character_outside_appearance_chapters():
    screenplay = sample_screenplay()
    target = next(
        scene for scene in screenplay.scenes if scene.source_chapter.startswith("第三章")
    )
    target.characters = ["character-1"]
    target.characters_present = ["character-1"]

    findings = check_continuity(screenplay)

    assert "character_absent" in kinds(findings)
    finding = next(item for item in findings if item.kind == "character_absent")
    assert finding.severity == "high"
    assert finding.scene_id == target.id


def test_detects_speaker_missing_from_present_list():
    screenplay = sample_screenplay()
    screenplay.scenes[0].characters_present = []

    findings = check_continuity(screenplay)

    assert "speaker_absent" in kinds(findings)
    assert next(item for item in findings if item.kind == "speaker_absent").severity == "high"


def test_detects_unknown_location():
    screenplay = sample_screenplay()
    screenplay.global_state.locations = [
        GlobalLocationState(
            id="location-1",
            name="渡口",
            first_appearance=screenplay.scenes[0].source_chapter,
            appearance_chapters=[screenplay.scenes[0].source_chapter],
            description="江边渡口。",
        )
    ]
    scene = screenplay.scenes[0]
    scene.location = "月球基地"
    scene.heading = f"{scene.int_ext} 月球基地 - {scene.time_of_day}"

    findings = check_continuity(screenplay)

    assert "unknown_location" in kinds(findings)


def test_chapter_placeholder_location_is_not_reported():
    """本地转换器抽不到地点时会退化成章节标题，这不算跨章矛盾。"""
    screenplay = sample_screenplay()
    screenplay.global_state.locations = [
        GlobalLocationState(
            id="location-1",
            name="渡口",
            first_appearance=screenplay.scenes[0].source_chapter,
            appearance_chapters=[screenplay.scenes[0].source_chapter],
            description="江边渡口。",
        )
    ]
    scene = screenplay.scenes[0]
    scene.location = "第一章"
    scene.heading = f"{scene.int_ext} 第一章 - {scene.time_of_day}"

    assert "unknown_location" not in kinds(check_continuity(screenplay))


def test_detects_timeline_disorder():
    screenplay = sample_screenplay()
    screenplay.scenes = [screenplay.scenes[2], screenplay.scenes[0], screenplay.scenes[1]]
    for index, scene in enumerate(screenplay.scenes, start=1):
        scene.id = f"scene-{index}"

    findings = check_continuity(screenplay)

    assert "timeline_disorder" in kinds(findings)


def test_findings_sorted_by_severity():
    screenplay = sample_screenplay()
    screenplay.scenes[0].characters_present = []  # high
    screenplay.scenes = [screenplay.scenes[2], screenplay.scenes[0], screenplay.scenes[1]]
    for index, scene in enumerate(screenplay.scenes, start=1):
        scene.id = f"scene-{index}"

    findings = check_continuity(screenplay)
    severities = [item.severity for item in findings]

    assert severities == sorted(severities, key=lambda value: {"high": 0, "medium": 1, "low": 2}[value])


def test_summarize_findings_counts_by_kind():
    screenplay = sample_screenplay()
    screenplay.scenes[0].characters_present = []

    summary = summarize_findings(check_continuity(screenplay))

    assert summary["total"] >= 1
    assert summary["high"] >= 1
    assert "speaker_absent" in summary["by_kind"]


# ---------------------------------------------------------------- 埋点与 AI


def test_continuity_check_records_metrics():
    check_continuity(sample_screenplay())

    row = metrics.summary()["tasks"]["continuity_check"]
    assert row["calls"] == 1
    event = next(item for item in metrics.recent_events() if item.get("kind") == "continuity_check")
    assert event["extra"]["scene_count"] >= 3


def test_ai_mode_adds_arc_drift_findings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        prompts.append(prompt)
        payload = {
            "findings": [
                {"scene_id": "scene-1", "detail": "林夏的目标与其弧光相反。", "suggestion": "调整潜台词。"}
            ]
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    findings = check_continuity(
        sample_screenplay(),
        mode="ai",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert CONTINUITY_PROMPT_MARKER in prompts[0]
    drift = [item for item in findings if item.kind == "arc_drift"]
    assert len(drift) == 1
    assert drift[0].severity == "low"


def test_ai_failure_falls_back_to_local_rules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    screenplay = sample_screenplay()
    screenplay.scenes[0].characters_present = []

    findings = check_continuity(
        screenplay, mode="ai", client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    # LLM 复核失败不影响本地规则的结论
    assert "speaker_absent" in kinds(findings)
    assert "arc_drift" not in kinds(findings)
