"""Source-grounded dialogue extraction; ambiguous speakers are never guessed."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass


_QUOTES = re.compile(r'“([^”\n]+)”|"([^"\n]+)"|「([^」\n]+)」')
_SPEECH = r"(?:说道|问道|答道|喊道|叫道|低声道|沉声道|冷声道|笑道|叹道|怒道|喝道|回答|反问|说|问|喊|答|道)"
_PUNCTUATION = re.compile(r"[\s，。！？、,.!?：:；;“”\"'「」（）()—-…]+")


def normalized_quote(text: str) -> str:
    return _PUNCTUATION.sub("", text).lower()


@dataclass(frozen=True)
class SourceDialogue:
    speaker: str
    text: str
    start: int
    end: int
    explicit: bool = True


def extract_dialogues(text: str, known_names: set[str]) -> list[SourceDialogue]:
    """Read every quote, including postposed speech tags and A addressing B.

    A known name followed by an action is retained for the local demo, but is
    marked non-explicit and cannot overrule an AI's attribution. Pronouns and
    untagged alternating turns are deliberately left unresolved.
    """
    if not known_names:
        return []
    names = "|".join(re.escape(name) for name in sorted(known_names, key=lambda n: (-len(n), n)))
    prefix_tag = re.compile(
        rf"(?P<name>{names})(?:对|向|冲|朝)(?:{names})"
        rf"[^。！？!?；;，,\n“”\"]{{0,8}}?{_SPEECH}[：:,，\s]*$"
        rf"|(?P<single>{names})[^。！？!?；;，,\n“”\"]{{0,12}}?"
        rf"{_SPEECH}[：:,，\s]*$"
    )
    suffix_tag = re.compile(
        rf"^[，,\s]*(?P<name>{names})(?P<tail>[^。！？!?；;\n“”\"]{{0,20}})"
    )
    quotes = list(_QUOTES.finditer(text))
    result: list[SourceDialogue] = []
    for index, quote in enumerate(quotes):
        previous_end = quotes[index - 1].end() if index else 0
        prefix = text[max(previous_end, quote.start() - 48):quote.start()]
        tag = prefix_tag.search(prefix)
        speaker, explicit = "", True
        if tag:
            speaker = tag.group("name") or tag.group("single")
            if tag.start() and prefix[tag.start() - 1] in "对向冲朝":
                speaker = ""
            # A name later in the tag normally denotes the addressee only
            # with an explicit 对/向/冲/朝 relation; otherwise do not guess.
            tail = tag.group(0)[len(speaker):]
            if any(name in tail for name in known_names) and not tag.group("name"):
                speaker = ""
        if not speaker:
            next_start = quotes[index + 1].start() if index + 1 < len(quotes) else len(text)
            suffix = text[quote.end():min(next_start, quote.end() + 40)]
            tag = suffix_tag.match(suffix)
            if tag:
                tail = tag.group("tail")
                # “甲的话。”乙说：“乙的话。” belongs to the NEXT quote.
                next_tag = prefix_tag.search(suffix)
                starts_next_turn = (index + 1 < len(quotes) and next_tag is not None
                                    and next_tag.start() == tag.start("name"))
                if not starts_next_turn:
                    explicit = bool(re.match(rf"[^，,]{{0,8}}?{_SPEECH}(?:[，,。！？!?]|$)", tail))
                    action = re.match(r"(?:握|手持|转身|抬|拿|放|站|走|坐|望|看|皱|点头|摇头)", tail)
                    if explicit or action:
                        speaker = tag.group("name")
        if speaker:
            result.append(SourceDialogue(speaker, next(v for v in quote.groups() if v),
                                         quote.start(), quote.end(), explicit))
    return result


def repair_attributions(elements: list[dict], evidence: list[tuple[str, str]]) -> int:
    """Correct ONLY exact normalized quotes with one unambiguous source speaker.

    No fuzzy match, new dialogue or deletion: paraphrases and repeated quotes
    spoken by different characters must remain a review concern.
    """
    speakers: dict[str, set[str]] = defaultdict(set)
    for speaker, quote in evidence:
        speakers[normalized_quote(quote)].add(speaker)
    corrected = 0
    for element in elements:
        if element.get("type") != "dialogue":
            continue
        choices = speakers.get(normalized_quote(element.get("text", "")), set())
        if len(choices) == 1 and element.get("character") not in choices:
            element["character"] = next(iter(choices))
            corrected += 1
    return corrected
