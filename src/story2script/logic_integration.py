"""Contracts between language-model extraction and executable story rules."""

from .logic_engine import event_for_change, event_matches_change, reason_story
from .logic_models import Event
from .plot_state import PlotIssue
from .plot_state import screenplay_hash


EVENT_RULES = """
事件集：death/exit/revive/return、transfer(actor,target,item)、move(actor,destination)、
learn(actor,secret,source_character 或 source_item 二选一)、relationship(actor,target,relationship)、
act(actor)、use_item(actor,item)。不用的字段必须留空，不得自造事件或跳过状态变化。
扩展事件使用 custom(actor,custom_rule,bindings)，必须引用已声明的 custom_rules。
不能把受伤、被捕等状态变化仅标为 act；自定义定义在规划确认后冻结。
规则：行动需 alive=true 且 available=true；死亡后两者 false；复活需 alive=false；回归需
alive=true 且 available=false。转交需双方可行动、交出者持有物品、双方 at 相同；移动需已知
原地点。获知秘密需来源人物知道且与学习者同地点，或学习者持有 contains(secret)=true 的物品。
关系 ally/enemy/neutral 双向更新，双方均可行动。requires 可补充 at/owner/knows/relationship
等必要前提，但不能替代核心规则。使用某条线索做判断的 act 必须带 knows 前提。
"""

CUSTOM_EVENT_PROTOCOL = """
用户选择 custom 时，按自然语言要求生成可审阅规则，不要求用户写 JSON。
logic.custom_properties 声明额外状态：id=custom:英文小写名、中文 name、subject_kind、
target_kind（无关系对象为空）、有限 values；symmetric=true 表示双向关系。
usable_values 非空时，角色只有这些状态才能行动，物品只有这些状态才能使用/转交/读取线索；
状态未知也阻止行动，必须诚实列出相关初始事实或假设。没有此限制就留空。
logic.custom_rules 包含 id、name、description、parameters（参数名→实体类型）；actor 是隐式角色参数。
active_roles 指定需要可行动的角色参数，默认 [actor]。病人、被释放者可以被动参与，但 requires
要检查其存活、患病/受困等条件，不能把实际行动者标为被动来逃避检查。
requires 至少一个前提；effects 只修改 custom: 状态；steps 组合内置事件，全部成功才原子提交。
参数引用仅支持整个字段为 $actor 或 $参数名；无表达式、外部代码、算术和嵌套自定义调用。
生死、available、owner、knows 等内置状态必须通过 steps 执行内置规则，不可直接 effects 修改。
lock_effects=true 表示用户要求持续锁定结果，只有显式批准的新事件可改变；一般事件用 false。
旧版本的规则和属性定义必须原样保留，变更定义需创建新 ID，不能重解释已发生的历史事件。
target_event.kind=custom、custom_rule 引用规则 ID，bindings 绑定所有额外参数；target/item 等留空。
例如受重伤：custom:health=[healthy,injured]、usable_values=[healthy]；injury 需要 health=healthy，
effects 设 injured；recovery 可 active_roles=[]，requires 需要 alive=true、health=injured，effects 设 healthy。
这是规则示例；初始条件不能倒填本次事件结果，语义不明应列 unresolved_questions。
"""

LOGIC_EXTRACTION = (
    """
logic 是可执行事件账本，不能仅写自然语言依赖。entities 给人物/物品/地点/秘密稳定唯一 ID；
人物 ID 必须与剧本一致。initial_facts 只表示全剧开始前的状态，不能把后续得到的物品或秘密
倒填到开头。确定状态给逐字正文 evidence；所有正文没有确证的初始状态都标 assumption=true，
explanation 解释假设。未知事实不填。禁止为通过规则检查捏造已知状态。
每个场景用 SceneLogic 标注所有事件，events 顺序按正文发生顺序。每个事件必须用
evidence={scene_id,element_index,quote} 引用本场 elements（索引从 0 开始）。
coverage 恰好覆盖每个正文元素；event_ids 指向该元素所包含的全部事件，无事件的环境/提及
才可标 context_only=true 并说明原因。动作不能伪装成背景。对白隐含行动由 act 表示。
初始谓词：alive/available 的 value 为 true/false；at 的 value 为地点 ID；owner 主体为物品，
value 为角色 ID；knows 主体为角色、target 为秘密 ID、value=true/false；contains 主体为物品，
target 为秘密 ID；relationship 主体和 target 是两个角色、value 为 ally/enemy/neutral。
source_novel 或旧稿只是参考，改编 constraints 才是当前版本事实。不能用未来事实证明当前行动。
历史回忆和录音在历史快照中验证，不得修改当下状态。初始假设最终由用户在计划中确认。
initial_source、initial_source_hash 由服务端保存，输出时留空。
"""
    + EVENT_RULES
    + CUSTOM_EVENT_PROTOCOL
)

SCENE_LOGIC_PROMPT = (
    """
SceneReview.logic 只返回 scene_id、events、coverage，不输出整份 StoryLogic 或初始状态。
使用已冻结的 logic_entities 和初始状态，不新增实体、
不修改 initial_facts。忠实提取当前正文里全部状态变化、行动与依赖；覆盖每个 elements。
如果正文缺少前提，返回真实事件让程序报告缺失；不能为了通过检查删除事件或忽略动作。
文字中的每次出场、行为都提取 act 或具体事件；仅提及、环境描述可以 context_only。
不能把用户要求的事件当作已经在正文发生，必须有实际正文证据。
每个事件 evidence 指向本场 scene_id、element_index（从0起）、逐字 quote；事件顺序匹配正文。
coverage 每个元素恰好一项，event_ids 登记该元素全部事件；没有事件才用 context_only=true。
事件 ID 全剧唯一，不使用 initial:/speech:/presence:/intervention: 保留前缀。
自定义事件引用上下文冻结的 custom_rules/custom_properties，不输出或修改它们的定义。
"""
    + EVENT_RULES
)


def capture_initial_source(story, base):
    previous = base.plot_state.logic if base.plot_state else None
    if previous and previous.initial_facts == story.initial_facts and previous.initial_source:
        story.initial_source = dict(previous.initial_source)
        story.initial_source_hash = previous.initial_source_hash
    else:
        story.initial_source = {
            s.id: [e.text for e in s.elements]
            for s in base.scenes
            if any(e.scene_id == s.id for f in story.initial_facts for e in f.evidence)
        }
        story.initial_source_hash = screenplay_hash(base)
    return story


def resolve_change_event(change, event: Event, story, requested):
    if event.kind != change.operation or event.actor != change.character_id:
        raise ValueError("事件解析的操作或执行角色与用户选择不一致。")
    entities = {e.id: e for e in story.entities}
    for name in (
        "target",
        "item",
        "destination",
        "secret",
        "relationship",
        "source_character",
        "source_item",
    ):
        supplied = getattr(requested, name, "")
        actual = getattr(event, name)
        if supplied:
            if name == "relationship":
                resolved = supplied
            elif supplied in entities:
                resolved = supplied
            else:
                matches = [e.id for e in story.entities if e.name == supplied]
                if len(matches) != 1:
                    raise ValueError(f"事件参数 {name} 无法唯一解析：{supplied}")
                resolved = matches[0]
            if resolved != actual:
                raise ValueError(f"模型解析的 {name} 与用户指定内容不一致。")
        setattr(change, name, actual)
    if event.kind == "custom":
        if requested.custom_rule and event.custom_rule != requested.custom_rule:
            raise ValueError("模型选择的自定义规则与用户指定不一致。")
        if not any(r.id == event.custom_rule for r in story.custom_rules):
            raise ValueError("目标事件缺少自定义规则定义。")
        for key, supplied in requested.bindings.items():
            matches = [e.id for e in story.entities if e.id == supplied or e.name == supplied]
            if len(set(matches)) != 1 or event.bindings.get(key) != matches[0]:
                raise ValueError(f"自定义参数 {key} 与用户指定不一致或存在歧义。")
    elif requested.custom_rule or requested.bindings or event.custom_rule or event.bindings:
        raise ValueError("内置事件不能携带自定义规则参数。")
    change.custom_rule = event.custom_rule
    change.bindings = dict(event.bindings)
    change.requires = [f.model_copy(deep=True) for f in event.requires]
    return change


def preserve_registered_rules(story, base):
    previous = base.plot_state.logic if base.plot_state else None
    if previous:
        for key in ("custom_rules", "custom_properties"):
            current = {v.id: v for v in getattr(story, key)}
            for old in getattr(previous, key):
                if current.get(old.id) != old:
                    raise ValueError(
                        f"已使用的 {old.id} 定义不能删除或改写；请使用新规则/状态 ID。"
                    )


def analyze_plan(plan, base):
    prior = base.plot_state.changes if base.plot_state else []
    baseline = reason_story(base, plan.logic, prior)
    after = reason_story(base, plan.logic, prior, intervention=plan.change)
    plan.baseline_reasoning = baseline
    plan.intervention_reasoning = after
    affected = {plan.change.effective_scene_id}
    existing = {s.event_id: s for s in baseline.steps}
    for step in after.steps:
        before = existing.get(step.event_id)
        if before and (step.status != before.status or step.effects != before.effects):
            affected.add(step.scene_id)
    dependencies = {sid: set(values) for sid, values in baseline.dependencies.items()}
    for dep in plan.dependencies:
        dependencies.setdefault(dep.scene_id, set()).update(dep.depends_on_scene_ids)
    for entry in plan.scenes:
        if entry.scene_id in affected or affected.intersection(
            dependencies.get(entry.scene_id, set())
        ):
            affected.add(entry.scene_id)
            entry.rewrite = True
            if entry.scene_id != plan.change.effective_scene_id:
                entry.impact = "indirect" if entry.impact == "scan" else entry.impact
            if "规则推演" not in entry.reason:
                entry.reason += "；规则推演发现本场前提或上游事件受到影响。"
    plan.estimated_calls = sum(s.rewrite for s in plan.scenes) + len(plan.scenes) + 1
    return plan


def generation_ready(plan):
    if plan.logic is None or plan.baseline_reasoning is None or plan.intervention_reasoning is None:
        raise ValueError("该计划来自旧版语义检查流程，请重新预览以建立事件账本。")
    if not plan.baseline_reasoning.valid:
        raise ValueError("原剧本的事件账本存在矛盾或未知前提，请先校验并修正。")
    target_id = event_for_change(plan.change).id
    target = next((s for s in plan.intervention_reasoning.steps if s.event_id == target_id), None)
    if target is None or target.status != "applied":
        raise ValueError("当前变更本身的前提不成立，请修正事件参数或初始事实。")
    if any(
        i.code == "goal" and i.scene_id == plan.change.effective_scene_id
        for i in plan.intervention_reasoning.issues
    ):
        raise ValueError("本次事件与已有的场末变更冲突，请选择新的生效场景或重新描述。")
    if plan.estimated_calls > plan.max_calls:
        raise ValueError("预计调用数超过预算，请提高上限后重新规划。")


def logic_issues(report):
    return [
        PlotIssue(
            scene_id=i.scene_id,
            kind="causality",
            message=f"规则 {i.code} / {i.event_id}：{i.message}",
        )
        for i in report.issues
    ]


def verify_goal(report, story, change):
    target = next((s for s in story.scenes if s.scene_id == change.effective_scene_id), None)
    matches = [e for e in target.events if event_matches_change(e, change)] if target else []
    applied = {s.event_id for s in report.steps if s.status == "applied"}
    return len(matches) == 1 and matches[0].id in applied
