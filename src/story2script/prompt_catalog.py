"""Stable prompt identifiers and source fingerprints for experiment tracking."""

from __future__ import annotations

import hashlib
import inspect
import textwrap
from functools import lru_cache
from typing import Callable

CONVERSION_CHUNK_PROMPT = "conversion.chapter_chunk"
CHARACTER_PROFILE_PROMPT = "characters.profile"
SCENE_REVIEW_PROMPT = "scene.review"
SCENE_REWRITE_PROMPT = "scene.rewrite"
SCENE_CHAT_PROMPT = "scene.chat_intent"
AGENT_PLANNER_PROMPT = "agent.planner"
TEAM_SUPERVISOR_PROMPT = "agent.team_supervisor"
CONTINUITY_REVIEW_PROMPT = "continuity.arc_review"

PROMPT_VERSION = "1"


def _fingerprint(builder: Callable) -> str:
    source = textwrap.dedent(inspect.getsource(builder)).replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"v{PROMPT_VERSION}:sha256:{digest}"


@lru_cache(maxsize=1)
def current_prompt_versions() -> dict[str, str]:
    """Return source-derived versions without importing prompt owners at module load."""
    from .agent.core import AdaptationAgent
    from .agent.team import AdaptationTeam
    from .character_profiles_ai import AICharacterProfiler
    from .continuity import _build_ai_prompt
    from .converter import AIConverter
    from .scene_chat import build_intent_prompt
    from .scene_review import AISceneReviewer
    from .scene_rewrite import AISceneRewriter

    builders = {
        CONVERSION_CHUNK_PROMPT: AIConverter._convert_chapter_chunk,
        CHARACTER_PROFILE_PROMPT: AICharacterProfiler._build_prompt,
        SCENE_REVIEW_PROMPT: AISceneReviewer._build_prompt,
        SCENE_REWRITE_PROMPT: AISceneRewriter._build_prompt,
        SCENE_CHAT_PROMPT: build_intent_prompt,
        AGENT_PLANNER_PROMPT: AdaptationAgent._build_planner_prompt,
        TEAM_SUPERVISOR_PROMPT: AdaptationTeam._build_supervisor_prompt,
        CONTINUITY_REVIEW_PROMPT: _build_ai_prompt,
    }
    return {prompt_id: _fingerprint(builder) for prompt_id, builder in builders.items()}
