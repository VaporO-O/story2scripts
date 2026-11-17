import pytest

from story2script.parser import parse_chapters


def test_parse_three_chinese_chapters() -> None:
    chapters = parse_chapters("第一章 开始\n内容一\n第二章 转折\n内容二\n第三章 结局\n内容三")

    assert len(chapters) == 3
    assert chapters[0].title == "第一章 开始"
    assert chapters[1].content == "内容二"


def test_parse_chapter_heading_with_digits() -> None:
    chapters = parse_chapters("第1章 开始\n内容一\n第2章 中段\n内容二\n第3章 结束\n内容三")

    assert [chapter.title for chapter in chapters] == ["第1章 开始", "第2章 中段", "第3章 结束"]


def test_parse_english_chapters() -> None:
    chapters = parse_chapters("Chapter 1 Start\none\nChapter 2 Middle\ntwo\nChapter 3 End\nthree")

    assert len(chapters) == 3
    assert chapters[2].content == "three"


def test_accept_one_or_two_chapters_and_unheaded_text():
    assert len(parse_chapters("第一章 开始\n内容一\n第二章 结尾\n内容二")) == 2
    assert len(parse_chapters("第一章 开始\n内容一")) == 1
    single = parse_chapters("林夏站在车站，等一封迟来的信。")
    assert single[0].title == "正文" and "迟来的信" in single[0].content


@pytest.mark.parametrize("text", ["", "  ", "第一章 开始\n第二章 结束"])
def test_reject_empty_content(text):
    with pytest.raises(ValueError, match="正文"):
        parse_chapters(text)


def test_preface_and_windows_newlines_preserved():
    chapters = parse_chapters("\ufeff这是前言。\r\n\r\n第一章 开始\r\n正文。")
    assert [(c.title, c.content) for c in chapters] == [
        ("前言", "这是前言。"),
        ("第一章 开始", "正文。"),
    ]


def test_table_of_contents_is_not_a_story_chapter():
    chapters = parse_chapters("目 录\n第一章 甲\n第二章 乙\n\n第一章 甲\n正文一\n第二章 乙\n正文二")
    assert len(chapters) == 2
