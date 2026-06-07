from pathlib import Path


EXAMPLES_DIR = Path(__file__).parents[2] / "examples"


def load_example_novel() -> dict[str, str]:
    novel_text = (EXAMPLES_DIR / "低智商犯罪(example).txt").read_text(encoding="utf-8")
    return {
        "title": "低智商犯罪",
        "genre": "悬疑 / 犯罪",
        "novel_text": novel_text,
    }

