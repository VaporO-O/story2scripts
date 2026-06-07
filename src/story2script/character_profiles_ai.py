import json
from typing import Protocol

from .character_profiles import extract_character_profiles
from .llm_client import LLMClient, loads_json_object
from .parser import Chapter

PROFILE_PLACEHOLDERS = {"待作者进一步补充", "待作者进一步补充。", "", "暂无", "无"}
PROFILE_FIELDS = ("role", "personality", "goal", "relationships", "key_change")


class CharacterProfiler(Protocol):
    mode: str

    def extract(self, chapters: list[Chapter]) -> list[dict]: ...


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "、".join(item.strip() for item in value if isinstance(item, str) and item.strip())
    if value is None:
        return ""
    return str(value).strip()


def _as_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    text = _as_text(value)
    return [text] if text else []


def _is_meaningful(text: str) -> bool:
    return bool(text) and text not in PROFILE_PLACEHOLDERS


class DemoCharacterProfiler:
    """本地规则人物小传提取器（默认，无需 API Key）。"""

    mode = "demo"

    def extract(self, chapters: list[Chapter]) -> list[dict]:
        return extract_character_profiles(chapters)


class AICharacterProfiler:
    """OpenAI-compatible 人物小传增强提取器。

    人物名单与出场章节仍由本地规则确定（稳定、可复现），LLM 只负责理解原文、补全
    性格、目标、人物关系和关键变化等需要语义理解的字段，避免模型新增/漏掉人物。
    """

    mode = "ai"

    def __init__(self, llm_client: LLMClient | None = None, client=None) -> None:
        self.llm_client = llm_client or LLMClient(client=client, usage_label="AI character profiles")

    def extract(self, chapters: list[Chapter]) -> list[dict]:
        base_profiles = extract_character_profiles(chapters)
        if not base_profiles:
            return base_profiles

        prompt = self._build_prompt(chapters, base_profiles)
        content = self.llm_client.complete_json(prompt)
        try:
            payload = loads_json_object(content)
        except ValueError as exc:
            raise ValueError(f"AI 人物小传失败：模型返回的内容不是合法 JSON。{exc}") from exc

        raw_profiles = payload.get("profiles") if isinstance(payload, dict) else payload
        if not isinstance(raw_profiles, list):
            raise ValueError("AI 人物小传失败：模型没有返回 profiles 列表。")

        ai_by_name: dict[str, dict] = {}
        for item in raw_profiles:
            if isinstance(item, dict):
                name = _as_text(item.get("name"))
                if name:
                    ai_by_name[name] = item

        return [self._merge_profile(base, ai_by_name.get(base["name"], {})) for base in base_profiles]

    def _merge_profile(self, base: dict, ai: dict) -> dict:
        """以本地结果为基底，仅在 LLM 给出有意义内容时覆盖语义字段。"""
        merged = dict(base)
        candidates = {
            "role": _as_text(ai.get("role")),
            "personality": _as_text(ai.get("personality")),
            "goal": _as_text(ai.get("goal")),
            "key_change": _as_text(ai.get("key_change")),
        }
        for field, value in candidates.items():
            if _is_meaningful(value):
                merged[field] = value

        relationships = [item for item in _as_text_list(ai.get("relationships")) if _is_meaningful(item)]
        if relationships:
            merged["relationships"] = relationships
        # name 与 appearance_chapters 保持本地权威值，杜绝 LLM 改名或新增人物。
        merged["name"] = base["name"]
        merged["appearance_chapters"] = base["appearance_chapters"]
        return merged

    def _build_prompt(self, chapters: list[Chapter], base_profiles: list[dict]) -> str:
        roster = [
            {"name": profile["name"], "appearance_chapters": profile["appearance_chapters"]}
            for profile in base_profiles
        ]
        source_text = "\n\n".join(f"{chapter.title}\n{chapter.content}" for chapter in chapters)
        return (
            "你是专业影视编剧和文学分析助手。请阅读小说原文，为给定的人物列表补全人物小传。\n"
            "只返回可被 json.loads 解析的 JSON 对象，形如 {\"profiles\": [...]}，"
            "不要 Markdown、不要解释文字。\n"
            "人物列表是固定的：名字必须原样使用，不要新增、删除或合并人物，"
            "也不要把旁观群众、机构或地名当成人物。\n"
            "每个 profile 必须包含字段：name, role, personality, goal, relationships, key_change。\n"
            "- name：必须与给定人物列表中的名字完全一致；\n"
            "- role：该人物在故事中的定位（如 主角 / 反派 / 关键配角 / 配角），结合剧情判断；\n"
            "- personality：用顿号（、）连接的 2~4 个性格关键词，必须有原文依据，不要泛泛而谈；\n"
            "- goal：该人物在故事中的核心目标或动机，用一句话概括；\n"
            "- relationships：字符串数组，描述与其他人物的关系"
            "（如 \"是高栋的下属\"、\"与周卫东对立\"），没有可写空数组；\n"
            "- key_change：该人物的关键转变或人物弧光，用一句话概括；"
            "若原文没有明显转变，写\"暂无明显转变\"。\n"
            f"人物列表（含已识别的出场章节）：{json.dumps(roster, ensure_ascii=False)}\n"
            f"小说原文：\n{source_text}"
        )


def get_character_profiler(mode: str = "demo", client=None) -> CharacterProfiler:
    if mode == "demo":
        return DemoCharacterProfiler()
    if mode == "ai":
        return AICharacterProfiler(client=client)
    raise ValueError(f"不支持的人物小传模式：{mode}")
