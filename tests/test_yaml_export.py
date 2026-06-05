import yaml

from story2script.converter import DemoConverter
from story2script.parser import parse_chapters
from story2script.screenplay import Screenplay
from story2script.yaml_export import screenplay_to_yaml


def test_screenplay_to_yaml_can_round_trip() -> None:
    chapters = parse_chapters("第一章 A\n林夏说：“走吧。”\n第二章 B\n内容二\n第三章 C\n内容三")
    screenplay = DemoConverter().convert(chapters, title="测试故事", genre="剧情")

    yaml_text = screenplay_to_yaml(screenplay)
    data = yaml.safe_load(yaml_text)
    restored = Screenplay.model_validate(data)

    assert restored.title == "测试故事"
    assert restored.source.chapter_count == 3
    assert "林夏" in yaml_text

