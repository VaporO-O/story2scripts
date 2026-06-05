import pytest

from story2script.parser import parse_chapters


def test_parse_three_chinese_chapters() -> None:
    chapters = parse_chapters(
        "第一章 开始\n内容一\n"
        "第二章 转折\n内容二\n"
        "第三章 结局\n内容三"
    )

    assert len(chapters) == 3
    assert chapters[0].title == "第一章 开始"
    assert chapters[1].content == "内容二"


def test_parse_chapter_heading_with_digits() -> None:
    chapters = parse_chapters("第1章 开始\n内容一\n第2章 中段\n内容二\n第3章 结束\n内容三")

    assert [chapter.title for chapter in chapters] == ["第1章 开始", "第2章 中段", "第3章 结束"]


def test_parse_english_chapters() -> None:
    chapters = parse_chapters(
        "Chapter 1 Start\none\nChapter 2 Middle\ntwo\nChapter 3 End\nthree"
    )

    assert len(chapters) == 3
    assert chapters[2].content == "three"


def test_reject_less_than_three_valid_chapters() -> None:
    with pytest.raises(ValueError, match="3 个章节"):
        parse_chapters("第一章 开始\n内容一\n第二章 结尾\n内容二")

