"""Story2Script MCP 服务：把小说→剧本工作台能力暴露为 MCP 工具。

设计要点：
- 服务端工作区持有小说与剧本状态，工具间用短 ID（novel-N / sp-N）引用，
  避免客户端模型在每次调用里回显整份小说或 YAML。
- 变更类工具（convert/rewrite/review）只返回紧凑摘要；全文经
  get_screenplay_yaml / save_screenplay 单独获取。
- 可能调用 LLM 的工具一律 anyio.to_thread.run_sync，不阻塞事件循环。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import anyio
from mcp.server.fastmcp import Context, FastMCP

from .api_models import ConvertRequest
from .character_profiles_ai import get_character_profiler
from .conversion_jobs import conversion_jobs
from .examples import load_example_novel
from .llm_client import _load_env_file
from .novel_import import import_novel_content
from .parser import parse_chapters
from .scene_review import (
    HumanVerdict,
    ReviewReport,
    merge_human_verdicts,
    review_and_improve,
    review_scenes_report,
)
from .scene_rewrite import OPERATION_MESSAGES
from .scene_rewrite import rewrite_scene as perform_scene_rewrite
from .screenplay import (
    SUPPORTED_ADAPTATION_TYPES,
    Screenplay,
    screenplay_json_schema,
)
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml

_POLL_INTERVAL_SECONDS = 0.2
_MAX_POLLS = 9000  # 30 分钟
_PREVIEW_CHARS = 200
_SCENE_SUMMARY_CHARS = 40


@dataclass
class StoredNovel:
    novel_text: str
    title: str = ""
    file_type: str = ""


@dataclass
class StoredScreenplay:
    screenplay: Screenplay
    report: ReviewReport | None = None


class Workspace:
    """进程内工作区：小说与剧本按短 ID 存取，线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._novels: dict[str, StoredNovel] = {}
        self._screenplays: dict[str, StoredScreenplay] = {}
        self._novel_seq = 0
        self._screenplay_seq = 0

    def add_novel(self, novel_text: str, title: str = "", file_type: str = "") -> str:
        with self._lock:
            self._novel_seq += 1
            novel_id = f"novel-{self._novel_seq}"
            self._novels[novel_id] = StoredNovel(novel_text, title, file_type)
            return novel_id

    def get_novel(self, novel_id: str) -> StoredNovel:
        with self._lock:
            if novel_id not in self._novels:
                raise ValueError(f"小说不存在：{novel_id}")
            return self._novels[novel_id]

    def add_screenplay(self, screenplay: Screenplay, report: ReviewReport | None = None) -> str:
        with self._lock:
            self._screenplay_seq += 1
            screenplay_id = f"sp-{self._screenplay_seq}"
            self._screenplays[screenplay_id] = StoredScreenplay(screenplay, report)
            return screenplay_id

    def get_screenplay(self, screenplay_id: str) -> StoredScreenplay:
        with self._lock:
            if screenplay_id not in self._screenplays:
                raise ValueError(f"剧本不存在：{screenplay_id}")
            return self._screenplays[screenplay_id]

    def update_screenplay(
        self,
        screenplay_id: str,
        screenplay: Screenplay | None = None,
        report: ReviewReport | None = None,
    ) -> None:
        with self._lock:
            entry = self._screenplays[screenplay_id]
            if screenplay is not None:
                entry.screenplay = screenplay
            if report is not None:
                entry.report = report

    def reset(self) -> None:
        with self._lock:
            self._novels.clear()
            self._screenplays.clear()
            self._novel_seq = 0
            self._screenplay_seq = 0


workspace = Workspace()

mcp = FastMCP(
    "story2script",
    instructions=(
        "Story2Script 小说改编剧本工作台。典型流程：get_example_novel 或 "
        "import_novel_file 拿到 novel_id → convert_novel 转换（demo 本地规则 / ai 大模型）"
        "得到 screenplay_id → review_screenplay 机审打分与自动修正 → "
        "rewrite_scene 局部重写 → save_screenplay 导出 YAML 与审校报告。"
    ),
)


def _resolve_novel_text(novel_id: str, novel_text: str) -> str:
    if novel_id and novel_text:
        raise ValueError("novel_id 与 novel_text 只能提供其中一个。")
    if novel_id:
        return workspace.get_novel(novel_id).novel_text
    if novel_text.strip():
        return novel_text
    raise ValueError("请提供 novel_id 或 novel_text。")


def _screenplay_summary(screenplay_id: str, entry: StoredScreenplay) -> dict:
    screenplay = entry.screenplay
    summary = {
        "screenplay_id": screenplay_id,
        "title": screenplay.title,
        "genre": screenplay.genre,
        "adaptation_type": screenplay.adaptation_type,
        "logline": screenplay.logline,
        "scene_count": len(screenplay.scenes),
        "characters": [character.name for character in screenplay.characters],
    }
    if entry.report is not None:
        summary["review_summary"] = entry.report.summary
    return summary


def _report_summary(report: ReviewReport) -> dict:
    return {
        "mode": report.mode,
        "threshold": report.threshold,
        "rounds_used": report.rounds_used,
        "summary": report.summary,
        "scenes": [
            {
                "scene_id": result.scene_id,
                "total": result.total,
                "verdict": result.verdict,
                "suggested_operation": result.suggested_operation,
                "issues": result.issues[:3],
            }
            for result in report.machine.values()
        ],
    }


def _chapter_preview_items(novel_text: str) -> list[dict]:
    chapters = parse_chapters(novel_text)
    return [
        {
            "index": index + 1,
            "title": chapter.title,
            "character_count": len(chapter.content),
            "preview": chapter.content[:60],
        }
        for index, chapter in enumerate(chapters)
    ]


@mcp.tool()
def get_example_novel() -> dict:
    """载入内置示例小说《低智商犯罪》到工作区，返回 novel_id、标题、题材与开头预览。"""
    example = load_example_novel()
    novel_id = workspace.add_novel(
        example["novel_text"], title=example["title"], file_type="example"
    )
    return {
        "novel_id": novel_id,
        "title": example["title"],
        "genre": example["genre"],
        "character_count": len(example["novel_text"]),
        "chapter_count": len(parse_chapters(example["novel_text"])),
        "preview": example["novel_text"][:_PREVIEW_CHARS],
    }


@mcp.tool()
def import_novel_file(file_path: str) -> dict:
    """从本地路径导入小说文件（TXT/Markdown/CSV/LOG/EPUB，≤25MB），存入工作区并返回 novel_id。"""
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ValueError(f"文件不存在：{file_path}")
    content_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
    imported = import_novel_content(path.name, content_base64)
    novel_id = workspace.add_novel(
        imported.novel_text, title=imported.title, file_type=imported.file_type
    )
    result = {
        "novel_id": novel_id,
        "file_name": imported.file_name,
        "file_type": imported.file_type,
        "title": imported.title,
        "character_count": imported.character_count,
        "preview": imported.novel_text[:_PREVIEW_CHARS],
    }
    try:
        result["chapter_count"] = len(parse_chapters(imported.novel_text))
    except ValueError as exc:
        result["chapter_count"] = 0
        result["warning"] = str(exc)
    return result


@mcp.tool()
def preview_chapters(novel_id: str = "", novel_text: str = "") -> dict:
    """预览章节切分结果（标题、字数、开头 60 字）。传 novel_id（推荐）或直接传 novel_text。"""
    text = _resolve_novel_text(novel_id, novel_text)
    chapters = _chapter_preview_items(text)
    return {"chapter_count": len(chapters), "chapters": chapters}


@mcp.tool()
async def convert_novel(
    novel_id: str = "",
    novel_text: str = "",
    title: str = "",
    genre: str = "",
    adaptation_type: str = "影视剧",
    mode: str = "demo",
    enable_review: bool = False,
    ctx: Context | None = None,
) -> dict:
    """把小说转换为结构化剧本，返回 screenplay_id 与摘要（不含全文，全文用 get_screenplay_yaml 取）。

    mode：demo 本地规则（秒级）/ ai 调用大模型（需配置 AI_API_KEY 等，分钟级，带进度）。
    enable_review 会在转换后自动机审并修正不达标场景。
    """
    text = _resolve_novel_text(novel_id, novel_text)
    if adaptation_type not in SUPPORTED_ADAPTATION_TYPES:
        supported = "、".join(SUPPORTED_ADAPTATION_TYPES)
        raise ValueError(f"不支持的改编类型：{adaptation_type}。可选：{supported}")
    request = ConvertRequest(
        novel_text=text,
        title=title,
        genre=genre,
        adaptation_type=adaptation_type,  # type: ignore[arg-type]
        mode=mode,
        enable_review=enable_review,
    )
    job_id = conversion_jobs.create(request).job_id

    last_progress: tuple[int, str, str] | None = None
    for _ in range(_MAX_POLLS):
        snapshot = conversion_jobs.snapshot(job_id)
        progress_key = (snapshot.progress, snapshot.stage, snapshot.message)
        if ctx is not None and progress_key != last_progress:
            await ctx.report_progress(
                snapshot.progress, 100, f"{snapshot.stage}：{snapshot.message}"
            )
            last_progress = progress_key
        if snapshot.status == "failed":
            raise ValueError(snapshot.error or "转换任务失败。")
        if snapshot.status == "succeeded":
            break
        await anyio.sleep(_POLL_INTERVAL_SECONDS)
    else:
        raise ValueError("转换超时（30 分钟）。请检查 AI 服务连通性，或改用 demo 模式。")

    result = snapshot.result
    if result is None:  # pragma: no cover - 防御性分支
        raise ValueError("转换任务未返回结果。")
    screenplay_id = workspace.add_screenplay(result.screenplay, report=result.review_report)
    summary = _screenplay_summary(screenplay_id, workspace.get_screenplay(screenplay_id))
    summary["mode"] = result.mode
    if result.review_report is not None:
        summary["review"] = _report_summary(result.review_report)
    return summary


@mcp.tool()
def list_scenes(screenplay_id: str) -> dict:
    """列出剧本全部场景的索引（id、slug line、摘要、出场人物）。"""
    entry = workspace.get_screenplay(screenplay_id)
    scenes = [
        {
            "id": scene.id,
            "heading": scene.heading,
            "summary": scene.summary[:_SCENE_SUMMARY_CHARS],
            "characters": scene.characters,
        }
        for scene in entry.screenplay.scenes
    ]
    return {"screenplay_id": screenplay_id, "scene_count": len(scenes), "scenes": scenes}


@mcp.tool()
def get_scene(screenplay_id: str, scene_id: str) -> dict:
    """获取单个场景的完整结构（对白、动作、戏剧化决策、镜头提示等）。"""
    entry = workspace.get_screenplay(screenplay_id)
    for scene in entry.screenplay.scenes:
        if scene.id == scene_id:
            return scene.model_dump(mode="json")
    raise ValueError(f"未找到场景：{scene_id}")


@mcp.tool()
async def rewrite_scene(
    screenplay_id: str,
    scene_id: str,
    operation: str,
    mode: str = "demo",
    character_id: str = "",
    tone: str = "更克制",
    feedback: str = "",
) -> dict:
    """对单个场景执行局部重写并更新工作区。

    operation 可选：rewrite_dialogue（重写对白）、strengthen_conflict（加强冲突）、
    short_drama_pace（短剧节奏）、add_camera_hints（补镜头提示）、
    reduce_narration（减少旁白）、adjust_character_voice（调整人物语气，需 character_id）。
    feedback 传审校意见可引导 AI 重写方向。
    """
    if operation not in OPERATION_MESSAGES:
        supported = "、".join(OPERATION_MESSAGES)
        raise ValueError(f"不支持的重写操作：{operation}。可选：{supported}")
    entry = workspace.get_screenplay(screenplay_id)

    def _work() -> tuple[Screenplay, str]:
        return perform_scene_rewrite(
            entry.screenplay,
            scene_id,
            operation,  # type: ignore[arg-type]
            character_id=character_id,
            tone=tone,
            mode=mode,  # type: ignore[arg-type]
            feedback=feedback,
        )

    updated, message = await anyio.to_thread.run_sync(_work)
    workspace.update_screenplay(screenplay_id, screenplay=updated)
    scene = next(scene for scene in updated.scenes if scene.id == scene_id)
    return {
        "screenplay_id": screenplay_id,
        "scene_id": scene_id,
        "operation": operation,
        "mode": mode,
        "message": message,
        "scene": scene.model_dump(mode="json"),
    }


@mcp.tool()
async def review_screenplay(
    screenplay_id: str,
    mode: str = "demo",
    auto_fix: bool = False,
    threshold: float | None = None,
    max_rounds: int | None = None,
    scene_ids: list[str] | None = None,
) -> dict:
    """机审剧本场景：按戏剧化/对白冲突/残留旁白/人物语气四项打分（0-10）。

    auto_fix 时对不达标场景带审校意见自动重写并复评（此时忽略 scene_ids）。
    threshold 默认读 AI_REVIEW_THRESHOLD（7.0），max_rounds 默认读 AI_REVIEW_MAX_ROUNDS（2）。
    """
    entry = workspace.get_screenplay(screenplay_id)

    if auto_fix:

        def _work() -> tuple[Screenplay, ReviewReport]:
            return review_and_improve(
                entry.screenplay,
                mode=mode,
                threshold=threshold,
                max_rounds=max_rounds,
                auto_fix=True,
            )

        updated, report = await anyio.to_thread.run_sync(_work)
        workspace.update_screenplay(screenplay_id, screenplay=updated, report=report)
    else:

        def _work() -> ReviewReport:
            return review_scenes_report(
                entry.screenplay,
                mode=mode,
                threshold=threshold,
                scene_ids=scene_ids or None,
            )

        report = await anyio.to_thread.run_sync(_work)
        workspace.update_screenplay(screenplay_id, report=report)

    return {
        "screenplay_id": screenplay_id,
        "auto_fix": auto_fix,
        **_report_summary(report),
    }


@mcp.tool()
def get_review_report(screenplay_id: str) -> dict:
    """获取完整审校报告（机审逐项得分 + 人审结论），JSON 结构。"""
    entry = workspace.get_screenplay(screenplay_id)
    if entry.report is None:
        raise ValueError("该剧本还没有审校报告，请先调用 review_screenplay。")
    return entry.report.model_dump(mode="json")


@mcp.tool()
def merge_human_review(screenplay_id: str, verdicts: list[dict]) -> dict:
    """把人审结论合并进审校报告。verdicts 形如
    [{"scene_id": "scene-1", "status": "approved|rejected|pending", "comment": "..."}]。"""
    entry = workspace.get_screenplay(screenplay_id)
    if entry.report is None:
        raise ValueError("该剧本还没有审校报告，请先调用 review_screenplay。")
    try:
        parsed = [HumanVerdict.model_validate(verdict) for verdict in verdicts]
    except Exception as exc:
        raise ValueError(f"人审结论格式不正确：{exc}") from exc
    merged = merge_human_verdicts(entry.report, parsed)
    workspace.update_screenplay(screenplay_id, report=merged)
    return {"screenplay_id": screenplay_id, "summary": merged.summary}


@mcp.tool()
def load_screenplay(yaml_text: str) -> dict:
    """校验剧本 YAML 并载入工作区，返回 screenplay_id 与摘要。用于继续编辑既有剧本。"""
    try:
        screenplay = screenplay_from_yaml(yaml_text)
    except Exception as exc:
        raise ValueError(f"YAML 校验失败：{exc}") from exc
    screenplay_id = workspace.add_screenplay(screenplay)
    return _screenplay_summary(screenplay_id, workspace.get_screenplay(screenplay_id))


@mcp.tool()
def get_screenplay_yaml(screenplay_id: str) -> str:
    """导出剧本的完整 YAML 文本。"""
    entry = workspace.get_screenplay(screenplay_id)
    return screenplay_to_yaml(entry.screenplay)


@mcp.tool()
def save_screenplay(screenplay_id: str, file_path: str, include_report: bool = False) -> dict:
    """把剧本 YAML 写入本地文件；include_report 时在旁边写 <名称>.review.json 审校报告。"""
    entry = workspace.get_screenplay(screenplay_id)
    path = Path(file_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(screenplay_to_yaml(entry.screenplay), encoding="utf-8")
    report_path: str | None = None
    if include_report:
        if entry.report is None:
            raise ValueError("该剧本还没有审校报告，请先调用 review_screenplay。")
        report_file = path.with_suffix(".review.json")
        report_file.write_text(
            json.dumps(entry.report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path = str(report_file)
    return {
        "screenplay_id": screenplay_id,
        "file_path": str(path),
        "report_path": report_path,
        "scene_count": len(entry.screenplay.scenes),
    }


@mcp.tool()
def validate_yaml(yaml_text: str) -> dict:
    """校验剧本 YAML 是否符合 Screenplay Schema，返回 {valid, message}，不入库。"""
    try:
        screenplay = screenplay_from_yaml(yaml_text)
    except Exception as exc:
        return {"valid": False, "message": f"YAML 校验失败：{exc}"}
    return {"valid": True, "message": f"YAML 校验通过，共 {len(screenplay.scenes)} 个场景。"}


@mcp.tool()
async def extract_character_profiles(
    novel_id: str = "", novel_text: str = "", mode: str = "demo"
) -> dict:
    """从小说中提取人物小传（角色定位、性格、目标、关系、关键转变）。mode：demo/ai。"""
    text = _resolve_novel_text(novel_id, novel_text)
    chapters = parse_chapters(text)
    profiler = get_character_profiler(mode)

    def _work() -> list[dict]:
        return profiler.extract(chapters)

    profiles = await anyio.to_thread.run_sync(_work)
    return {"mode": profiler.mode, "profiles": profiles}


@mcp.resource("screenplay://schema")
def screenplay_schema_resource() -> str:
    """剧本 YAML 对应的 JSON Schema 全文。"""
    return json.dumps(screenplay_json_schema(), ensure_ascii=False, indent=2)


def _apply_env_file(path_text: str) -> None:
    values = _load_env_file(Path(path_text).expanduser())
    for key, value in values.items():
        os.environ.setdefault(key, value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="story2script-mcp",
        description="Story2Script MCP 服务（stdio）。",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="启动前加载的 .env 文件路径（已存在的环境变量优先，不覆盖）。",
    )
    args = parser.parse_args(argv)
    if args.env_file:
        _apply_env_file(args.env_file)
    mcp.run()


if __name__ == "__main__":
    main()
