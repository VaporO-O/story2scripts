import re
from collections import Counter, defaultdict

from .parser import Chapter


# 常见单字姓氏（百家姓子集，覆盖绝大多数中文人名首字）。
# 人名识别的第一道闸门：候选词必须以姓氏开头，从根本上排除“直接 / 点头 /
# 马上 / 皱眉 / 幽幽地 / 继续”等动词、副词被误判成人名的情况。
SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双"
    "闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农"
    "温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘"
    "匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空"
    "曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

# 这些虚词、代词、介词几乎不会出现在中文人名里，一旦命中即判定候选词不是人名。
NAME_FORBIDDEN_CHARS = set(
    "的地得了着们过吗呢吧啊呀嘛你我他她它谁很太也都就又还没不是这那怎么啥之与和在把被让"
)

# 以姓氏开头、却是常见词的高频干扰项，单独列入黑名单。
NON_NAME_WORDS = {
    "周围", "周到", "周边", "周末", "周年", "方向", "方便", "方面", "方才", "方式",
    "方案", "方法", "方上", "高兴", "高高", "高低", "高大", "高手", "高峰", "高处",
    "高配", "高叔", "马上", "马路", "石头", "白手", "和田", "金秋", "时间", "时候",
    "时刻", "任务", "任何", "安排", "安全", "安静", "毕竟", "毕业", "应该", "应付",
    "应答", "计划", "何况", "单位", "管理", "全局", "全市", "全国", "全省", "全程",
    "全部", "全力", "全中", "成绩", "成功", "纪委", "危险", "解释", "解决", "支烟",
    "明白", "明显", "明天", "明明", "常务", "黄金", "能力", "关系", "从容", "空降",
    "古怪", "于是", "强自", "尹始", "冷声", "沉声", "高声", "厉声", "明知", "毕恭",
    "何必", "何苦",
}

# 表示地点、机构或物件的词尾，出现在词尾即判定该候选词不是人名（如“公安局 /
# 公安部 / 黄金店 / 高厅”）。
PLACE_OBJECT_SUFFIX = set("店楼室局部厅案信车包枪馆院街路桥港站城村镇厂行司队组科处所园区")

# 明显是动词的词尾，人名几乎不会以此收尾（如“冷声喝”来自“冷声喝道”）。
VERB_SUFFIX = set("说喊喝吼嚷哼叹问")

# 人名后接的称谓，用于把“吴主任 / 赵主任 / 陈法医 / 齐局长”识别成人物。
TITLE_SUFFIXES = (
    "厅长", "副厅长", "局长", "副局长", "大队长", "中队长", "队长", "主任", "法医",
    "厂长", "科长", "处长", "所长", "主席", "市长", "副市长", "书记", "老板",
)
# 人名前出现的称谓，是很强的人物信号（如“副局长卢正 / 刑大队长叶剑 / 老师张宁”）。
TITLE_PREFIXES = (
    "厅长", "副厅长", "局长", "副局长", "大队长", "中队长", "队长", "主任", "法医",
    "老师", "同学", "师傅", "老板", "队员", "警官", "商人",
)

PERSONALITY_KEYWORDS = [
    "敏感", "固执", "观察力强", "冷静", "勇敢", "谨慎", "冲动", "温柔", "孤僻", "理性",
    "沉稳", "狡猾", "暴躁", "果断", "多疑",
]
RELATION_TERMS = ["姐姐", "弟弟", "哥哥", "妹妹", "父亲", "母亲", "老师", "同学", "侄子", "叔叔"]

# 强信号：候选词紧贴“说 / 问 / 道 …”并带引号，是最可靠的“说话人”证据。
_SPEECH_TAGS = (
    "说道|说|问道|问|喊道|喊|叫道|叫|答道|答|低声道|沉声道|冷声道|怒道|笑道|叹道|喝道|道"
)
QUOTE_SPEAKER_PATTERN_TMPL = rf"{{name}}[^，。！？\n]{{{{0,4}}}}?(?:{_SPEECH_TAGS})[：:，,]?[“\"]"
# 中等信号：候选词位于句首/分句首，且很快接上动作或言说动词（典型的“主语+谓语”）。
_SUBJECT_VERBS = (
    "说|问|道|喊|答|笑|叹|吼|点头|摇头|站|走|坐|看|望|瞧|转身|转过|回头|抬|掏|拿|持|皱"
    "|瞪|盯|愣|哼|想|觉得|知道|继续|起身|思索|沉默|迟疑|纠正|争辩|寻思|表态|应付|挥|拍"
)
SUBJECT_PATTERN_TMPL = (
    rf"(?:^|[。！？!?；;，,、：:“”\"\n]){{name}}[^，。！？\n]{{{{0,6}}}}?(?:{_SUBJECT_VERBS})"
)
# 中等信号：候选词被引入或成为“查 / 抓 / 举报 …”这类指向人的动词的宾语。
_INTRO_VERBS = "叫|名叫|自称|人称"
_PERSON_OBJECT_VERBS = "查|调查|找|寻找|杀|抓|抓住|举报|对付|陷害|怀疑|跟踪|捅|救|派"
INTRO_PATTERN_TMPL = rf"(?:{_INTRO_VERBS}|{_PERSON_OBJECT_VERBS}){{name}}"

RELATION_PATTERN = re.compile(
    rf"(?P<name>[一-鿿]{{2,4}})是"
    rf"(?P<other>[一-鿿]{{2,4}})的(?P<relation>{'|'.join(RELATION_TERMS)})"
)
CHANGE_PATTERN = re.compile(
    r"(?P<name>[一-鿿]{2,4}).{0,16}从(?P<start>[^，。！？\n]{1,12})到(?P<end>[^，。！？\n]{1,12})"
)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]


def _passes_name_gate(token: str) -> bool:
    """判断候选词是否具备人名的基本形态（姓氏开头、长度合理、无虚词、非地点物件）。"""
    if not (2 <= len(token) <= 3):
        return False
    if token[0] not in SURNAMES:
        return False
    if any(ch in NAME_FORBIDDEN_CHARS for ch in token):
        return False
    if token in NON_NAME_WORDS:
        return False
    if any(token.endswith(suffix) for suffix in TITLE_SUFFIXES):
        return True
    if token[-1] in PLACE_OBJECT_SUFFIX or token[-1] in VERB_SUFFIX:
        return False
    return True


def _scan_candidates(text: str) -> tuple[Counter, Counter]:
    """统计以姓氏开头的 2~3 字候选词频次，以及它们前一个汉字的搭配频次。"""
    counts: Counter[str] = Counter()
    left_context: Counter[tuple[str, str]] = Counter()
    length_of_text = len(text)
    for index, char in enumerate(text):
        if char not in SURNAMES:
            continue
        for length in (2, 3):
            end = index + length
            if end > length_of_text:
                continue
            token = text[index:end]
            if not all("一" <= ch <= "鿿" for ch in token):
                continue
            counts[token] += 1
            if index > 0 and "一" <= text[index - 1] <= "鿿":
                left_context[(text[index - 1], token)] += 1
    return counts, left_context


def _is_substring_artifact(token: str, counts: Counter, left_context: Counter) -> bool:
    """判断候选词是否只是更长词的片段（如“江口”来自“三江口”、“卫东”来自“周卫东”）。"""
    total = counts[token]
    for (prev_char, candidate), pair_count in left_context.items():
        if candidate == token and pair_count >= total * 0.8:
            return True
    return False


def _person_evidence(name: str, text: str) -> str:
    """返回候选词的人物证据等级：strong / title / medium / none。"""
    escaped = re.escape(name)
    if re.search(QUOTE_SPEAKER_PATTERN_TMPL.format(name=escaped), text):
        return "strong"
    if re.search(rf"(?:{'|'.join(TITLE_PREFIXES)}){escaped}", text):
        return "strong"
    if re.search(rf"{escaped}是[一-鿿]{{2,4}}的(?:{'|'.join(RELATION_TERMS)})", text):
        return "strong"
    # 姓氏 + 称谓（吴主任 / 陈法医）是人物，但要求至少出现两次，避免“齐局长”这类
    # 仅出现一次的称谓性别名混入。
    if any(name.endswith(suffix) for suffix in TITLE_SUFFIXES):
        return "title"
    if re.search(SUBJECT_PATTERN_TMPL.format(name=escaped), text):
        return "medium"
    if re.search(INTRO_PATTERN_TMPL.format(name=escaped), text):
        return "medium"
    return "none"


def _detect_character_names(text: str) -> dict[str, int]:
    """从小说全文中识别人物姓名，返回 {姓名: 出现次数}。

    识别链路：姓氏闸门 + 虚词/地点过滤 -> 长度消歧（“张一昂”胜过“张一”，“高栋”
    胜过“高栋继”）-> 去除更长词的片段 -> 结合“说话人 / 称谓 / 主语谓语 / 被指向”
    等人物证据与词频做接纳判定。无法稳定判定的长尾留给后续 LLM 增强。
    """
    counts, left_context = _scan_candidates(text)
    gated = {token: count for token, count in counts.items() if _passes_name_gate(token)}

    # 长度消歧：三字候选若其两字前缀更常见，说明三字是“前缀+尾字”的偶然组合；
    # 前缀本身是黑名单成语片段（“明知”→“明知故”）或后缀本身是更高频的真实姓名
    # （“周荣”→“查周荣”）时，同样判定三字候选是干扰项。
    resolved: dict[str, int] = dict(gated)
    for token in list(gated):
        if len(token) != 3:
            continue
        prefix, suffix = token[:2], token[1:]
        if prefix in NON_NAME_WORDS:
            resolved.pop(token, None)
        elif suffix[0] in SURNAMES and counts.get(suffix, 0) > gated[token]:
            resolved.pop(token, None)
        elif gated[token] >= counts.get(prefix, 0) * 0.7:
            # 两字前缀几乎总是延伸成这个三字词（如“张一”→“张一昂”），三字才是真名。
            resolved.pop(prefix, None)
        else:
            # 三字词只是“真名+尾字”的偶然组合（如“林夏在”来自“林夏在码头”）。
            resolved.pop(token, None)

    names: dict[str, int] = {}
    for token, count in resolved.items():
        if _is_substring_artifact(token, counts, left_context):
            continue
        evidence = _person_evidence(token, text)
        if evidence == "strong":
            names[token] = count
        elif evidence == "title" and count >= 2:
            names[token] = count
        elif evidence == "medium" and count >= 3:
            names[token] = count
        elif len(token) == 3 and count >= 4:
            # 高频三字姓名（如“齐振兴”）即便缺少显式动词证据也予以保留；
            # 常见双字干扰词（“能力 / 关系”等）已被黑名单与长度限制排除。
            names[token] = count
    return names


def _goal_for(name: str, text: str) -> str:
    triggers = ["想要", "希望", "决定", "发誓", "试图", "目标是", "打算"]
    action_triggers = ["寻找", "找到", "查明", "调查"]
    for sentence in _sentences(text):
        if name not in sentence:
            continue
        for trigger in triggers:
            if trigger in sentence:
                return sentence.split(trigger, 1)[1].strip("，,：: ")
        for trigger in action_triggers:
            if trigger in sentence:
                return f"{trigger}{sentence.split(trigger, 1)[1]}".strip("，,：: ")
    return "待作者进一步补充。"


def _personalities_for(name: str, text: str) -> list[str]:
    traits: list[str] = []
    for sentence in _sentences(text):
        if name not in sentence:
            continue
        for keyword in PERSONALITY_KEYWORDS:
            if keyword in sentence and keyword not in traits:
                traits.append(keyword)
    return traits or ["待作者进一步补充"]


def _key_change_for(name: str, text: str) -> str:
    for sentence in _sentences(text):
        if name not in sentence:
            continue
        sentence_change = re.search(
            r"从(?P<start>[^，。！？\n]{1,12})到(?P<end>[^，。！？\n]{1,12})",
            sentence,
        )
        if sentence_change:
            return f"从{sentence_change.group('start')}到{sentence_change.group('end')}"

    for match in CHANGE_PATTERN.finditer(text):
        if match.group("name") == name:
            return f"从{match.group('start')}到{match.group('end')}"
    return "待作者进一步补充。"


def extract_character_profiles(chapters: list[Chapter]) -> list[dict]:
    """Extract lightweight character profile tables from parsed novel chapters."""
    full_text = "\n".join(chapter.content for chapter in chapters)
    name_counts = _detect_character_names(full_text)
    names = list(name_counts)

    relationships: dict[str, list[str]] = defaultdict(list)
    for match in RELATION_PATTERN.finditer(full_text):
        name = match.group("name")
        other = match.group("other")
        relation = match.group("relation")
        if name not in name_counts:
            continue
        relationships[name].append(f"是{other}的{relation}")

    appearances: dict[str, list[str]] = {}
    for name in names:
        appearances[name] = [chapter.title for chapter in chapters if name in chapter.content]

    ordered_names = sorted(
        names,
        key=lambda item: (-len(appearances[item]), -name_counts[item], item),
    )
    profiles: list[dict] = []
    for index, name in enumerate(ordered_names):
        profiles.append(
            {
                "name": name,
                "role": "主角" if index == 0 else "配角",
                "personality": "、".join(_personalities_for(name, full_text)),
                "goal": _goal_for(name, full_text),
                "relationships": relationships.get(name, ["待作者进一步补充。"]),
                "appearance_chapters": appearances[name],
                "key_change": _key_change_for(name, full_text),
            }
        )

    return profiles
