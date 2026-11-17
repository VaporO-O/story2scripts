import re
from dataclasses import dataclass


CHAPTER_HEADING_PATTERN = re.compile(
    r"(?im)^[ \t]*(第[零一二两三四五六七八九十百千万\d]+章[^\r\n]*|chapter\s+\d+[^\r\n]*)[ \t]*$"
)


@dataclass(frozen=True)
class Chapter:
    title: str
    content: str


def parse_chapters(novel_text: str) -> list[Chapter]:
    """Accept any nonempty story; unheaded prose is one chapter, never fabricated padding."""
    novel_text = novel_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not novel_text.strip():
        raise ValueError("请提供非空小说正文。")
    matches = list(CHAPTER_HEADING_PATTERN.finditer(novel_text))
    if not matches:
        return [Chapter(title="正文", content=novel_text.strip())]
    chapters: list[Chapter] = []
    prefix = novel_text[:matches[0].start()].strip()
    # Do not turn a table-of-contents label into a story chapter. Real prefatory
    # prose is retained instead of silently discarding text before chapter 1.
    if prefix and not re.fullmatch(r"(?i)(目\s*录|contents|table\s+of\s+contents)", prefix):
        chapters.append(Chapter(title="前言", content=prefix))

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(novel_text)
        content = novel_text[start:end].strip()
        if content:
            chapters.append(Chapter(title=match.group(1).strip(), content=content))

    if not chapters:
        raise ValueError("章节标题下没有正文，请补充小说内容。")

    return chapters

