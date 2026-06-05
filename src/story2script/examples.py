from pathlib import Path


EXAMPLES_DIR = Path(__file__).parents[2] / "examples"


def load_example_novel() -> dict[str, str]:
    novel_text = (EXAMPLES_DIR / "novel.txt").read_text(encoding="utf-8")
    return {
        "title": "雾港来信",
        "genre": "悬疑 / 剧情",
        "novel_text": novel_text,
    }

