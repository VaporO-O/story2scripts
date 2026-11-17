# 自然语言自定义剧情事件

更新日期：2026-11-08。支持用户描述事件，由模型提出可执行的声明式规则，再由程序推演。已有八种内置操作继续保留。

## 使用方法

进入“剧情改写”，选择角色、生效场景和“自定义事件”，直接输入例如：

> 林夏在这一场末受重伤，在康复之前不能继续调查，让周宁接替后续行动。

点击预览，核对“本次自定义事件”、初始事实及“额外状态与事件规则”。模型应明确提出健康状态、受伤前提、受伤结果，以及仅在健康时可行动的限制。确认后生成候选。之后可在已有版本上继续输入“林夏康复”，模型应沿用已有健康状态定义。

普通用户无需手写 JSON。高级编辑器允许修改本计划新建的规则；已存在于原稿版本的规则不能覆盖或删除，需要新的规则 ID。规则确认与初始事实确认共用复选框，修改账本会使旧确认失效。

## 两种组合能力

**额外状态**：给角色/物品/地点/秘密增加有限取值的属性，如健康、拘束、物品完好程度、身份公开程度或天气。属性必须使用 `custom:` 前缀，声明主体/对象类型和可选值；可选择双向更新。

**组合内置步骤**：把转交物品、关系变化、信息获取等内置操作组合成一个事件。依次在临时状态上执行，任何一步失败，所有效果撤销。成功后状态来源指向整个自定义事件，内部步骤也保留在证明中。

原有 alive、available、owner、knows、at 等状态不能通过自定义 effects 直接覆盖，必须调用内置步骤。例如“交出钥匙并告知秘密”仍需满足持有钥匙、双方同地、告知者知道秘密等前提。

## 声明式规则示例

以下定义放在 StoryLogic 的 `custom_properties` 和 `custom_rules`；初始条件还需给所有受健康限制的角色明确设置健康状态或假设。

```json
{
  "custom_properties": [
    {
      "id": "custom:health",
      "name": "健康状态",
      "subject_kind": "character",
      "target_kind": "",
      "values": ["健康", "受伤"],
      "symmetric": false,
      "usable_values": ["健康"]
    }
  ],
  "custom_rules": [
    {
      "id": "injury",
      "name": "受重伤",
      "description": "角色受伤后不能行动，直至康复。",
      "parameters": {},
      "active_roles": ["actor"],
      "requires": [{"subject": "$actor", "predicate": "custom:health", "value": "健康"}],
      "steps": [],
      "effects": [{"subject": "$actor", "predicate": "custom:health", "value": "受伤"}],
      "lock_effects": false
    },
    {
      "id": "recovery",
      "name": "康复",
      "description": "受伤且仍存活的角色康复。",
      "parameters": {},
      "active_roles": [],
      "requires": [
        {"subject": "$actor", "predicate": "alive", "value": "true"},
        {"subject": "$actor", "predicate": "custom:health", "value": "受伤"}
      ],
      "steps": [],
      "effects": [{"subject": "$actor", "predicate": "custom:health", "value": "健康"}],
      "lock_effects": false
    }
  ]
}
```

对应正文事件为 `kind: custom`、`actor: character-1`、`custom_rule: injury`、`bindings: {}`，并附该场正文 evidence 和 coverage。可在 `/api/plot/plan` 仅指定 `operation: custom` 和自然语言 instruction；模型负责提取上述定义及实例。想复用指定规则时可传 custom_rule 和 bindings，参数值支持已有实体 ID 或唯一名称。

## 参数、条件与限制

`actor` 是一个隐式角色参数；额外 `parameters` 声明参数名及 character/item/location/secret 类型。事件的 bindings 必须恰好绑定全部额外参数。模板只支持整个字符串为 `$actor`、`$recipient` 这样的参数引用，不支持字符串插值、公式、代码、网络访问或递归规则。

`active_roles` 中的角色必须存活、available=true，并满足所有已声明的可行动状态；缺失状态不会被假定为正常。被动参与者可以不列入，但必须在 requires 中表达必要前提。例如医生需要可行动，接受治疗的病人可以受伤；不能把实际行动者标为被动以绕过规则。

`usable_values` 不为空时，角色行动或物品转交/使用/线索读取会自动检查此属性；为空时，该属性只有被显式 requires 引用才影响后续剧情。若定义了“受伤”却不设置行动限制，引擎不会自行推断受伤程度。名称“昏迷”“损毁”本身没有隐藏执行含义。

`lock_effects: true` 表示用户要求持续保持事件结果，只有显式批准的新事件才能改变它；false 允许后续有依据、前提成立的事件更新状态。默认 false。死者复活等内置锁仍遵循原规则。

## 可表达与暂不支持

受伤/康复、被捕/获释、物品损毁/修复、身份变化、婚姻变化，以及若干内置步骤组合都可以通过上述状态模型表达。它们不是预先写死的所有故事规则，模型提出的定义需要确认。

暂不支持任意代码、数值资源运算、持续时间/概率、量词、任意嵌套条件、自动世界知识完备推断、同场多时间线、增删人物或增删重排场景。环境事件仍需选择一个相关角色作为事件锚点；可将该角色设为被动参与者，用地点属性描述天气等变化。

目前只验证已登记状态和事件。模型漏提行为、误读证据、遗漏附加前提，依然可能影响结论。正文引用证明出处存在，不单独证明语义正确。长篇真实小说的抽取准确率和新事件创作质量尚未做真实模型评测。

实现：`logic_models.py`、`custom_events.py`、`logic_engine.py`；测试：`tests/test_custom_events.py`。模型提取、定义校验、原稿重放、变更推演、逐场改写、采用前重检均沿用同一套规则。
