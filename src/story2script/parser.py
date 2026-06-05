import re
from dataclasses import dataclass


CHAPTER_HEADING_PATTERN = re.compile(
    r"(?im)^\s*(第[零一二两三四五六七八九十百千万\d]+章[^\r\n]*|chapter\s+\d+[^\r\n]*)\s*$"
)


@dataclass(frozen=True)
class Chapter:
    title: str
    content: str


def parse_chapters(novel_text: str) -> list[Chapter]:
    """Split novel text into chapters using common Chinese or English headings."""
    matches = list(CHAPTER_HEADING_PATTERN.finditer(novel_text))
    chapters: list[Chapter] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(novel_text)
        content = novel_text[start:end].strip()
        if content:
            chapters.append(Chapter(title=match.group(1).strip(), content=content))

    if len(chapters) < 3:
        raise ValueError(
            "至少需要识别出 3 个章节。请使用“第一章 标题”或“Chapter 1 Title”格式。"
        )

    return chapters

