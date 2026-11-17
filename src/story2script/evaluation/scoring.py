"""确定性评测指标与一致性故障注入。"""

from __future__ import annotations

import json
import re

from ..continuity import check_continuity
from ..converter import _split_scene_slices, _text_units
from ..parser import Chapter
from ..screenplay import Dialogue, Screenplay
from .models import (
    AttributionMetrics,
    BehaviorMetrics,
    ContinuityFault,
    ExpectedAnnotations,
    OutputMetrics,
    ScoreCounts,
    SourceMetrics,
)


class EvaluationDataError(ValueError):
    """Raised when gold annotations cannot be applied to their source case."""


def _ratio(numerator: int, denominator: int, empty: float = 1.0) -> float:
    if denominator == 0:
        return empty
    return round(numerator / denominator, 4)


def score_sets(expected: set, predicted: set) -> ScoreCounts:
    correct = len(expected & predicted)
    precision = _ratio(correct, len(predicted), empty=1.0 if not expected else 0.0)
    recall = _ratio(correct, len(expected), empty=1.0)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    return ScoreCounts(
        expected=len(expected),
        predicted=len(predicted),
        correct=correct,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _predicted_boundaries(chapter: Chapter) -> set[int]:
    slices = _split_scene_slices(chapter.content)
    boundaries: set[int] = set()
    consumed = 0
    for scene_slice in slices[:-1]:
        consumed += len(_text_units(scene_slice.text))
        boundaries.add(consumed)
    return boundaries


def score_scene_boundaries(
    chapters: list[Chapter], expected_by_chapter: dict[str, list[int]]
) -> ScoreCounts:
    expected: set[tuple[str, int]] = set()
    predicted: set[tuple[str, int]] = set()
    for chapter in chapters:
        units = _text_units(chapter.content)
        for boundary in expected_by_chapter.get(chapter.title, []):
            if boundary >= len(units):
                raise EvaluationDataError(
                    f"{chapter.title} 的标注边界 {boundary} 超出文本单元范围（{len(units)}）。"
                )
            expected.add((chapter.title, boundary))
        predicted.update((chapter.title, value) for value in _predicted_boundaries(chapter))
    return score_sets(expected, predicted)


_PUNCTUATION = re.compile(r"[\s，。！？、,.!?：:；;“”\"'（）()—-]+")


def _normalized_text(text: str) -> str:
    return _PUNCTUATION.sub("", text).lower()


def score_dialogue_attribution(
    screenplay: Screenplay, expected_annotations
) -> AttributionMetrics:
    rows = diagnose_dialogue_attribution(screenplay, expected_annotations)
    matched = sum(row["status"] != "unmatched" for row in rows)
    correct = sum(row["status"] == "correct" for row in rows)
    return AttributionMetrics(
        expected=len(rows), matched=matched, correct=correct,
        accuracy=_ratio(correct, len(rows)),
        quote_coverage=_ratio(matched, len(rows)),
        matched_speaker_accuracy=_ratio(correct, matched, empty=1.0 if not rows else 0.0),
        wrong_speaker=matched - correct, unmatched=len(rows) - matched,
    )


def diagnose_dialogue_attribution(screenplay: Screenplay, expected_annotations) -> list[dict]:
    """Unmatched means missing OR paraphrased; it is not a speaker error.

    Exact matches precede substring matches. Matching never uses the gold
    speaker to select a candidate, and each actual utterance is used once.
    """
    names = {character.id: character.name for character in screenplay.characters}
    actual: list[tuple[str, str, str, str]] = []
    for scene in screenplay.scenes:
        for element in scene.elements:
            if isinstance(element, Dialogue):
                actual.append((_normalized_text(element.text), names.get(element.character, ""),
                               scene.id, element.text))

    used: set[int] = set()
    rows = []
    for annotation in expected_annotations:
        quote = _normalized_text(annotation.quote)
        candidates = [
            (0 if text == quote else 1, index)
            for index, (text, _speaker, _scene, _raw) in enumerate(actual)
            if index not in used and quote and text
            and (text == quote or (min(len(text), len(quote)) >= 4
                                  and (text in quote or quote in text)))
        ]
        candidate_index = min(candidates)[1] if candidates else None
        if candidate_index is None:
            rows.append({"quote": annotation.quote, "expected_speaker": annotation.speaker,
                         "status": "unmatched", "scene_id": "", "actual_speaker": "",
                         "actual_text": ""})
            continue
        used.add(candidate_index)
        _text, speaker, scene_id, raw = actual[candidate_index]
        rows.append({"quote": annotation.quote, "expected_speaker": annotation.speaker,
                     "status": "correct" if speaker == annotation.speaker else "wrong_speaker",
                     "scene_id": scene_id, "actual_speaker": speaker, "actual_text": raw})
    return rows


def score_output(screenplay: Screenplay, expected: ExpectedAnnotations) -> OutputMetrics:
    schema_valid = True
    try:
        Screenplay.model_validate(screenplay.model_dump(mode="json"))
    except ValueError:
        schema_valid = False

    actual_titles = screenplay.source.chapter_titles
    title_matches = sum(
        1 for expected_title, actual_title in zip(expected.chapter_titles, actual_titles)
        if expected_title == actual_title
    )
    title_accuracy = _ratio(title_matches, len(expected.chapter_titles))
    expected_names = set(expected.character_names)
    actual_names = {character.name for character in screenplay.characters}
    return OutputMetrics(
        schema_valid=schema_valid,
        chapter_title_accuracy=title_accuracy,
        characters=score_sets(expected_names, actual_names),
        dialogue_attribution=score_dialogue_attribution(
            screenplay, expected.dialogue_attributions
        ),
    )


def _inject_faults(
    screenplay: Screenplay, faults: list[ContinuityFault]
) -> tuple[Screenplay, set[tuple[str, str]]]:
    payload = screenplay.model_dump(mode="json")
    expected: set[tuple[str, str]] = set()
    chapters = payload["source"]["chapter_titles"]

    for fault in faults:
        scenes = payload["scenes"]
        if fault.scene_index >= len(scenes):
            raise EvaluationDataError(
                f"一致性故障的 scene_index={fault.scene_index} 超出场景数 {len(scenes)}。"
            )
        scene = scenes[fault.scene_index]
        scene_id = scene["id"]

        if fault.kind == "speaker_absent":
            dialogue = next(
                (element for element in scene["elements"] if element.get("type") == "dialogue"),
                None,
            )
            if dialogue is None:
                raise EvaluationDataError(f"{scene_id} 没有对白，无法注入 speaker_absent。")
            speaker = dialogue["character"]
            scene["characters_present"] = [
                item for item in scene["characters_present"] if item != speaker
            ]
            expected.add(("speaker_absent", scene_id))
        elif fault.kind == "unknown_location":
            location = "评测未知仓库"
            scene["location"] = location
            scene["heading"] = f"{scene['int_ext']} {location} - {scene['time_of_day']}"
            expected.add(("unknown_location", scene_id))
        elif fault.kind == "character_absent":
            if not scene["characters"]:
                raise EvaluationDataError(f"{scene_id} 没有人物，无法注入 character_absent。")
            character_id = scene["characters"][0]
            state = next(
                item for item in payload["global_state"]["characters"] if item["id"] == character_id
            )
            replacement = next(
                (chapter for chapter in chapters if chapter != scene["source_chapter"]),
                chapters[0],
            )
            state["first_appearance"] = replacement
            state["appearance_chapters"] = [replacement]
            for candidate in scenes:
                if (
                    character_id in candidate["characters"]
                    and candidate["source_chapter"] != replacement
                ):
                    expected.add(("character_absent", candidate["id"]))

    return Screenplay.model_validate(payload), expected


def score_continuity_probe(
    screenplay: Screenplay, faults: list[ContinuityFault]
) -> ScoreCounts:
    if not faults:
        predicted = {
            (finding.kind, finding.scene_id)
            for finding in check_continuity(screenplay, mode="demo")
        }
        return score_sets(set(), predicted)
    faulty, expected = _inject_faults(screenplay, faults)
    predicted = {
        (finding.kind, finding.scene_id)
        for finding in check_continuity(faulty, mode="demo")
    }
    return score_sets(expected, predicted)


def score_source(
    chapters: list[Chapter], screenplay: Screenplay, expected: ExpectedAnnotations
) -> SourceMetrics:
    return SourceMetrics(
        scene_boundaries=score_scene_boundaries(chapters, expected.scene_boundaries),
        continuity_probe=score_continuity_probe(screenplay, expected.continuity_faults),
    )


def score_behavior(trace, status: str, message: str) -> BehaviorMetrics:
    action_steps = [step for step in trace if step.action is not None]
    invalid = sum(
        1
        for step in action_steps
        if step.error or (isinstance(step.observation, dict) and step.observation.get("error"))
    )
    repeated = 0
    previous_signature = ""
    for step in action_steps:
        signature = json.dumps(step.action.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        if signature == previous_signature:
            repeated += 1
        previous_signature = signature
    count = len(action_steps)
    return BehaviorMetrics(
        action_count=count,
        invalid_action_count=invalid,
        repeated_action_count=repeated,
        tool_legal_rate=_ratio(count - invalid, count),
        repeated_action_rate=_ratio(repeated, count, empty=0.0),
        circuit_breaker_triggered=status == "failed" and "连续" in message,
    )
