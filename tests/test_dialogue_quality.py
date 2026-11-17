import pytest

from story2script.converter import DemoConverter
from story2script.dialogue import extract_dialogues, repair_attributions
from story2script.evaluation.models import DialogueAttribution
from story2script.evaluation.scoring import score_dialogue_attribution
from story2script.parser import parse_chapters


@pytest.mark.parametrize(("text", "expected"), [
    ('林夏说：“钥匙在这里。”周宁说：“我去取。”', [("林夏", "钥匙在这里。"), ("周宁", "我去取。")]),
    ('林夏对周宁说：“你先走。”', [("林夏", "你先走。")]),
    ('“钥匙在这里。”周宁答道。', [("周宁", "钥匙在这里。")]),
    ('“未知说话人。”周宁说：“我去取。”', [("周宁", "我去取。")]),
    ('林夏说：“好。”', [("林夏", "好。")]),
    ('“周宁”三个字写在纸上。', []),
    ('他对周宁说：“你先走。”', []),
    ('“先用旧设备。”林夏说。周宁说：“我去接线。”', [("林夏", "先用旧设备。"), ("周宁", "我去接线。")]),
])
def test_source_speaker_evidence(text, expected):
    assert [(item.speaker, item.text) for item in extract_dialogues(text, {"林夏", "周宁"})] == expected


def test_scene_preserves_multiple_speakers_and_postposed_only_character():
    screenplay = DemoConverter().convert(parse_chapters(
        '第一章 夜站\n林夏说：“钥匙在这里。”周宁说：“我去取。”\n'
        '第二章 仓库\n“不要开门。”陈默答道。\n第三章 归途\n林夏说：“等一下。”'
    ))
    names = {character.id: character.name for character in screenplay.characters}
    actual = [(names[element.character], element.text) for scene in screenplay.scenes
              for element in scene.elements if element.type == "dialogue"]
    assert actual == [("林夏", "钥匙在这里。"), ("周宁", "我去取。"),
                      ("陈默", "不要开门。"), ("林夏", "等一下。")]


def test_repair_requires_unique_exact_source_evidence():
    elements = [{"type": "dialogue", "character": "b", "text": "钥匙在柜中！"},
                {"type": "dialogue", "character": "b", "text": "柜子里放着钥匙"},
                {"type": "dialogue", "character": "b", "text": "好"}]
    assert repair_attributions(elements, [("a", "钥匙在柜中。"), ("a", "好"), ("b", "好")]) == 1
    assert [element["character"] for element in elements] == ["a", "b", "b"]


def test_metrics_separate_paraphrases_from_wrong_speakers():
    screenplay = DemoConverter().convert(parse_chapters(
        '第一章 开场\n林夏说：“钥匙在柜中。”周宁说：“我去取。”\n'
        '第二章 途中\n林夏说：“门已经锁了。”\n第三章 结尾\n周宁说：“走吧。”'
    ))
    result = score_dialogue_attribution(screenplay, [
        DialogueAttribution(quote="钥匙在柜中", speaker="林夏"),
        DialogueAttribution(quote="我去取", speaker="林夏"),
        DialogueAttribution(quote="柜子里的钥匙不见了", speaker="周宁"),
    ])
    assert result.correct == 1 and result.wrong_speaker == 1 and result.unmatched == 1
    assert result.quote_coverage == 0.6667
    assert result.matched_speaker_accuracy == 0.5


def test_ai_rewrite_repairs_exact_quote_speaker_without_mutating_input():
    import json
    from story2script.scene_rewrite import AISceneRewriter
    screenplay = DemoConverter().convert(parse_chapters(
        '第一章 开场\n林夏说：“钥匙在柜中。”周宁说：“我去取。”\n'
        '第二章 途中\n林夏说：“门已经锁了。”\n第三章 结尾\n周宁说：“走吧。”'
    ))
    target = screenplay.scenes[0]
    original = screenplay.model_dump(mode="json")
    dialogue = next(element for element in target.elements if element.type == "dialogue")
    wrong = next(character.id for character in screenplay.characters if character.id != dialogue.character)
    class Model:
        def complete_json(self, prompt, **kwargs):
            replacement = target.model_dump(mode="json")
            for element in replacement["elements"]:
                if element["type"] == "dialogue":
                    element["character"] = wrong
            return json.dumps(replacement, ensure_ascii=False)
    updated = AISceneRewriter(llm_client=Model()).rewrite(screenplay, target.id, "rewrite_dialogue")
    actual = next(element for element in updated.scenes[0].elements if element.type == "dialogue")
    assert actual.character == dialogue.character
    assert screenplay.model_dump(mode="json") == original
