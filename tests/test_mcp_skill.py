from pathlib import Path

import pytest
import yaml

from story2script.mcp_server import (
    build_novel_knowledge,
    convert_novel,
    get_example_novel,
    get_screenplay_yaml,
    list_scenes,
    mcp,
    preview_chapters,
    review_screenplay,
    run_adaptation_agent,
    save_screenplay,
    validate_yaml,
    workspace,
)


ROOT = Path(__file__).parents[1]
SKILL_DIR = ROOT / ".claude" / "skills" / "adapt-with-story2script"
SKILL_FILE = SKILL_DIR / "SKILL.md"
CONTRACT_FILE = SKILL_DIR / "references" / "tool-contracts.md"
OPENAI_FILE = SKILL_DIR / "agents" / "openai.yaml"

WORKFLOW_TOOLS = {
    "build_novel_knowledge",
    "convert_novel",
    "extract_character_profiles",
    "get_example_novel",
    "get_metrics",
    "get_review_report",
    "get_scene",
    "get_screenplay_yaml",
    "import_novel_file",
    "list_scenes",
    "load_agent_session",
    "load_screenplay",
    "load_team_session",
    "merge_human_review",
    "preview_chapters",
    "review_screenplay",
    "rewrite_scene",
    "run_adaptation_agent",
    "run_adaptation_team",
    "save_screenplay",
    "search_novel_knowledge",
    "validate_yaml",
}


def _skill_metadata(text: str) -> dict:
    assert text.startswith("---\n")
    frontmatter, _ = text.removeprefix("---\n").split("\n---\n", 1)
    return yaml.safe_load(frontmatter)


@pytest.fixture(autouse=True)
def clean_mcp_workspace():
    workspace.reset()
    yield
    workspace.reset()


def test_mcp_skill_metadata_is_discoverable_and_utf8() -> None:
    skill_text = SKILL_FILE.read_text(encoding="utf-8")
    contracts = CONTRACT_FILE.read_text(encoding="utf-8")
    openai_text = OPENAI_FILE.read_text(encoding="utf-8")
    metadata = _skill_metadata(skill_text)
    interface = yaml.safe_load(openai_text)["interface"]

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "adapt-with-story2script"
    assert "Story2Script MCP" in metadata["description"]
    assert "novel" in metadata["description"].lower()
    assert interface["display_name"] == "Story2Script 改编工作流"
    assert "$adapt-with-story2script" in interface["default_prompt"]
    assert "TODO" not in skill_text + contracts + openai_text
    assert "�" not in skill_text + contracts + openai_text


@pytest.mark.anyio
async def test_mcp_skill_only_references_registered_workflow_tools() -> None:
    docs = SKILL_FILE.read_text(encoding="utf-8") + CONTRACT_FILE.read_text(encoding="utf-8")
    registered = {tool.name for tool in await mcp.list_tools()}

    assert WORKFLOW_TOOLS <= registered
    assert all(f"`{name}`" in docs for name in WORKFLOW_TOOLS)


def test_mcp_skill_has_validation_and_restart_recovery() -> None:
    skill_text = SKILL_FILE.read_text(encoding="utf-8")

    assert "validate_yaml" in skill_text
    assert "load_agent_session" in skill_text
    assert "load_team_session" in skill_text
    assert "load_screenplay" in skill_text
    assert "Never silently fall back" in skill_text


@pytest.mark.anyio
async def test_documented_demo_workflow_reaches_valid_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_FILE_ROOTS", str(tmp_path))
    source = get_example_novel()

    preview = preview_chapters(novel_id=source["novel_id"])
    knowledge = await build_novel_knowledge(source["novel_id"], mode="demo")
    converted = await convert_novel(
        novel_id=source["novel_id"],
        title=source["title"],
        genre=source["genre"],
        adaptation_type="影视剧",
        mode="demo",
    )
    scene_index = list_scenes(converted["screenplay_id"])
    baseline = await review_screenplay(
        converted["screenplay_id"], mode="demo", auto_fix=False
    )
    agent_result = await run_adaptation_agent(
        converted["screenplay_id"],
        goal="让全部场景通过机审",
        mode="demo",
        max_steps=2,
        novel_id=source["novel_id"],
    )
    final_review = await review_screenplay(
        converted["screenplay_id"], mode="demo", auto_fix=False
    )
    yaml_text = get_screenplay_yaml(converted["screenplay_id"])
    validation = validate_yaml(yaml_text)
    saved = save_screenplay(
        converted["screenplay_id"], str(tmp_path / "screenplay.yaml"), include_report=True
    )

    assert preview["chapter_count"] >= 3
    assert knowledge["doc_count"] > 0
    assert scene_index["scene_count"] == converted["scene_count"]
    assert baseline["scenes"]
    assert agent_result["screenplay_id"] == converted["screenplay_id"]
    assert final_review["scenes"]
    assert validation["valid"] is True
    assert saved["file_path"] == str(tmp_path / "screenplay.yaml")
    assert saved["report_path"] == str(tmp_path / "screenplay.review.json")
