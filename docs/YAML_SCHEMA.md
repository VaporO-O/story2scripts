# Story2Script YAML Schema v1.0

本文档说明 Story2Script 当前 YAML 输出格式。示例统一使用项目内置小说《低智商犯罪》（悬疑 / 犯罪）。

Story2Script 的 YAML 不是最终拍摄台本，而是适合 AI 生成、作者编辑、程序校验和后续导出的结构化中间格式。

## 1. 设计目标

1. 支持三章以上小说转换为结构化剧本 YAML。
2. 支持本地规则转换和 AI 转换共用同一套数据结构。
3. 通过 `Screenplay.model_validate` 保证前端拿到的永远是合法结构。
4. 显式表达小说到剧本的改编判断，而不是机械搬运叙述。
5. 对齐工业级剧本格式，保留内外景、时间、地点、人物、道具等可制作性字段。
6. 用 `global_state` 保证长文本分块转换时的人物、地点和时间线一致。

机器可读 JSON Schema 位于：

```text
schema/screenplay.schema.json
```

Pydantic 模型位于：

```text
src/story2script/screenplay.py
```

## 2. 顶层结构

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 当前固定为 `1.0` |
| `title` | string | 是 | 剧本标题，不能为空 |
| `genre` | string | 是 | 类型，可为空字符串 |
| `adaptation_type` | enum | 是 | 改编类型 |
| `logline` | string | 是 | 一句话故事梗概，不能为空 |
| `source` | object | 是 | 原小说章节信息 |
| `global_state` | object | 是 | 跨章节一致性状态表 |
| `characters` | array | 是 | 顶层角色表 |
| `scenes` | array | 是 | 剧本场景列表，至少 1 项 |

示例：

```yaml
schema_version: '1.0'
title: 低智商犯罪
genre: 悬疑 / 犯罪
adaptation_type: 短剧
logline: 围绕《低智商犯罪》中一场失控抢劫与警方追查展开的剧本初稿。
source:
  chapter_count: 4
  chapter_titles:
    - 第一章
    - 第二章
    - 第三章
    - 第四章
global_state: {}
characters: []
scenes: []
```

## 3. 改编类型

`adaptation_type` 用于描述同一段小说要按哪种媒介和节奏改写。

| 值 | 输出特点 |
| --- | --- |
| `短剧` | 节奏快，冲突密集，强反转 |
| `影视剧` | 场景完整，镜头感强 |
| `舞台剧` | 舞台提示、人物走位更多 |
| `广播剧` | 音效、旁白、声音表演更多 |
| `分镜脚本` | 镜头、画面、景别更多 |

服务端会把该字段写入最终 `Screenplay`，并在本地转换和 AI prompt 中影响节奏、冲突密度和制作提示。

## 4. Source

`source` 描述原小说章节，用于场景追溯和引用校验。

```yaml
source:
  chapter_count: 4
  chapter_titles:
    - 第一章
    - 第二章
    - 第三章
    - 第四章
```

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `chapter_count` | integer | `>= 3` | 有效章节数量 |
| `chapter_titles` | array[string] | 至少 3 项 | 章节标题列表 |

应用层会校验：

- `chapter_count` 必须等于 `chapter_titles` 数量。
- `scene.source_chapter` 必须存在于 `chapter_titles`。
- `global_state` 中人物、地点、事件引用的章节必须存在于 `chapter_titles`。

## 5. Global State

`global_state` 是跨章节一致性引擎的核心。系统会先扫全文抽取人物表、地点表和时间线，再把这张表作为固定上下文传给全文转换和局部重写。

```yaml
global_state:
  characters:
    - id: character-1
      name: 方超
      aliases: []
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      traits:
        - 冒险
        - 主导
      goal: 带着刘直完成黄金店抢劫并脱身。
      arc: 从自信掌控局面到被不断升级的风险逼出破绽。
      consistency_note: 后续分块转换必须保持方超的主导性、犯罪计划和说话风格一致。
    - id: character-2
      name: 刘直
      aliases: []
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      traits:
        - 犹疑
      goal: 跟随方超完成行动并避免出错。
      arc: 从被动服从到在压力中暴露迟疑。
      consistency_note: 后续场景中保持刘直理解慢半拍、依赖方超判断的状态。
  locations:
    - id: location-1
      name: 酒店客房
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      description: 方超和刘直在行动前检查伪装、武器和背包。
    - id: location-2
      name: 黄金店
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      description: 方超和刘直计划抢劫的目标地点。
  timeline:
    - id: event-1
      order: 1
      chapter: 第一章
      time_marker: 下午
      summary: 方超和刘直在酒店客房完成伪装和行动前准备。
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `global_state.characters` | array | 跨章节人物状态表，ID 必须能在顶层 `characters` 找到 |
| `global_state.locations` | array | 地点状态表，记录首次出现和出现章节 |
| `global_state.timeline` | array | 章节级事件时间线 |

为什么需要它：

- 长小说分块转换时，LLM 容易把同一人物写成不同称呼，或让性格、目标漂移。
- `global_state` 让第 1 章和第 3 章出现的同一人物引用同一个稳定 ID。
- 局部重生成也会携带这张表，只改当前场景，不改跨章节事实。

## 6. Character

顶层 `characters` 是剧本实际引用的人物表。

```yaml
characters:
  - id: character-1
    name: 方超
    description: 行动主导者，负责制定抢劫计划并指挥刘直。
    motivation: 用精心设计的爆炸和交通混乱掩护抢劫。
    arc: 从自信掌控计划到在警方压力下暴露破绽。
  - id: character-2
    name: 刘直
    description: 方超的同伙，负责配合进入黄金店。
    motivation: 跟随方超完成行动并分得收益。
    arc: 从服从执行到因紧张和迟疑增加行动风险。
```

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 小写字母、数字、下划线、连字符 | 稳定人物 ID |
| `name` | string | 非空 | 人物名 |
| `description` | string | 可为空 | 人物简介 |
| `motivation` | string | 可为空 | 人物动机 |
| `arc` | string | 非空 | 人物弧光 |

对白、场景人物列表、`global_state.characters` 都通过 `id` 引用人物，避免人物改名或别名导致引用失效。

## 7. Scene

剧本主体由 `scenes` 组成。每个 scene 都必须同时包含戏剧要素和可制作性要素。

```yaml
scenes:
  - id: scene-1
    heading: INT. 酒店客房 - DAY
    int_ext: INT.
    time_of_day: DAY
    location: 酒店客房
    source_chapter: 第一章
    summary: 方超和刘直在酒店客房完成抢劫前的伪装和装备检查。
    goal: 方超要确认计划准备就绪，带刘直进入下一步行动。
    conflict: 刘直跟不上方超的黑色幽默和计划节奏，行动前的紧张感被不断放大。
    beat: 方超从社会抱怨转入行动命令，抢劫正式开始倒计时。
    subtext: 方超表面调侃社会浮躁，实则在为自己的犯罪行为寻找合理化借口。
    characters:
      - character-1
      - character-2
    characters_present:
      - character-1
      - character-2
    props:
      - 手枪
      - 双肩包
      - 假发
      - 胶皮手套
    dramatization_decisions:
      - source_text: 方超手持一把枪，站在酒店客房的窗户边。
        target: action
        rendering: 方超撩开窗帘，枪藏在手边，观察街对面的店铺。
        reason: 可见行为改写成动作行，直接建立危险状态。
      - source_text: “洗脚的又怎么了？”
        target: dialogue
        rendering: 洗脚的又怎么了？
        reason: 原文明确对白保留为台词，用于表现刘直的迟钝和两人关系。
      - source_text: 方超对社会浮躁的抱怨。
        target: subtext
        rendering: 方超把犯罪计划包装成对社会骗局的反击。
        reason: 价值判断不直接搬成旁白，而是转成潜台词和人物动机。
    elements:
      - type: action
        text: 方超站在酒店客房窗边，撩开窗帘，枪口压在掌心下。
      - type: dialogue
        character: character-1
        parenthetical: 盯着窗外
        emotion: 讥讽
        text: 你有没有感觉，现在的人普遍浮躁？
      - type: dialogue
        character: character-2
        parenthetical: 停下整理背包
        emotion: 困惑
        text: 洗脚的又怎么了？
      - type: action
        text: 方超检查假发和胡子，把手枪锁上保险，藏到腰后。
    camera_hints:
      - 近景：方超撩开窗帘观察街对面。
      - 特写：手枪保险被扣上。
```

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | string | `scene-数字` | 场景 ID |
| `heading` | string | 非空，必须匹配生产字段 | 标准场景标题 |
| `int_ext` | enum | `INT.` / `EXT.` | 内景 / 外景 |
| `time_of_day` | enum | `DAY` / `NIGHT` | 拍摄时段 |
| `location` | string | 非空 | 可拍摄地点 |
| `source_chapter` | string | 必须来自 `source.chapter_titles` | 来源章节 |
| `summary` | string | 非空 | 场景摘要 |
| `goal` | string | 非空 | 角色可见目标 |
| `conflict` | string | 非空 | 阻挡目标的冲突 |
| `beat` | string | 非空 | 节拍或转折点 |
| `subtext` | string | 非空 | 潜台词 |
| `characters` | array[string] | 人物 ID | 本场相关人物 |
| `characters_present` | array[string] | 人物 ID | 画面或舞台上实际在场人物 |
| `props` | array[string] | 可为空 | 道具 |
| `dramatization_decisions` | array | 至少 1 项 | 叙述到戏剧化分类 |
| `elements` | array | 至少 1 项 | 动作和对白 |
| `camera_hints` | array[string] | 可为空 | 镜头或调度提示 |

`heading` 必须与生产字段一致：

```text
{int_ext} {location} - {time_of_day}
```

例如：

```text
INT. 酒店客房 - DAY
EXT. 黄金店门口 - DAY
```

## 8. Scene Element

`elements` 是真正的剧本正文，按场景内顺序排列。

### Action

```yaml
- type: action
  text: 方超检查假发和胡子，把手枪锁上保险，藏到腰后。
```

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `type` | string | 固定为 `action` |
| `text` | string | 非空 |

动作行必须是可见或可听见的内容，尽量避免“他想起”“他意识到”这类不可拍摄心理句。

### Dialogue

```yaml
- type: dialogue
  character: character-1
  parenthetical: 低声
  emotion: 果断
  text: 动手！
```

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `type` | string | 固定为 `dialogue` |
| `character` | string | 必须引用顶层 `characters` 中存在的 ID |
| `parenthetical` | string | 可为空 |
| `emotion` | string | 可为空 |
| `text` | string | 非空 |

前端预览会根据 `type` 分别渲染动作和对白；AI 返回后，服务端会把人物名引用归一化为稳定 `character-id`。

## 9. Dramatization Decision

`dramatization_decisions` 记录“小说叙述 -> 剧本表达”的分类判断。

```yaml
dramatization_decisions:
  - source_text: 方超手持一把枪，站在酒店客房的窗户边。
    target: action
    rendering: 方超撩开窗帘，枪藏在手边，观察街对面的店铺。
    reason: 可见行为改写成动作行，直接建立危险状态。
```

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `source_text` | string | 非空 | 原文片段 |
| `target` | enum | 见下表 | 改写方向 |
| `rendering` | string | 非空 | 改写后的表达 |
| `reason` | string | 非空 | 分类原因 |

`target` 可选值：

| target | 用途 |
| --- | --- |
| `action` | 可见行为、身体反应、场面推进 |
| `dialogue` | 明确对白或需要通过信息交换推动冲突 |
| `subtext` | 心理活动、情绪判断、未说出口的意图 |
| `scene_description` | 天气、空间、背景、氛围 |

这个字段让 AI 改编过程可解释：评委不只看到结果，还能看到系统为什么把某段小说叙述改成动作、对白、潜台词或场景描述。

## 10. 场景拆分规则

系统会根据通用叙事信号拆分章节：

- 时间变化：下午、夜里、第二天、与此同时。
- 地点变化：进入、离开、抵达、转入新空间。
- 人物进出：新人物出现、进入现场、离开现场。
- 情节转折：信息突然变化，角色判断被迫调整。
- 冲突变化：质问、拒绝、威胁、争执、阻止、危险升级。

AI prompt 也会显式要求模型围绕这些信号输出场景，而不是只按自然段机械切分。

## 11. 叙述到戏剧化

小说里的心理描写、环境叙述和背景解释不能原样搬进剧本。Story2Script 会让系统或 LLM 做分类决策：

- 可见行为 -> `action`
- 明确说话内容 -> `dialogue`
- 心理活动和真实意图 -> `subtext`
- 环境、空间、气氛 -> `scene_description`

例如，《低智商犯罪》中方超对“社会浮躁”和“骗局”的抱怨，不应只作为长段旁白堆在动作里；它更适合被拆成：

- 方超观察街对面的动作。
- 方超带讥讽情绪的对白。
- 他为抢劫行为寻找合理化借口的潜台词。

这也是剧本和小说的本质区别：小说靠叙述，剧本靠冲突和动作推进。

## 12. AI 转换校验链路

AI 全文转换使用统一 LLM 客户端读取：

- `AI_API_KEY`
- `AI_BASE_URL`
- `AI_MODEL`
- `AI_TIMEOUT_SECONDS`
- `AI_MAX_TOKENS`

服务端调用 `/chat/completions`，要求模型返回 JSON。核心链路是：

```text
llm_json -> json.loads / tolerant parsing -> normalize -> Screenplay.model_validate -> screenplay_to_yaml
```

其中：

- 全文转换会先抽取 `global_state`，再按章节和章节内片段分块调用 LLM。
- 每次 AI 只返回 `{"scenes": [...]}`，顶层标题、人物表、类型和状态表由服务端合并。
- 模型输出被截断时会返回清晰错误，提示调高 `AI_MAX_TOKENS` 或减少单次文本规模。
- 校验失败时返回 `422`，不会把坏 YAML 给前端。

## 13. 局部重生成

局部重生成只替换目标 scene，不重新生成全文。

支持操作：

| operation | 说明 |
| --- | --- |
| `rewrite_dialogue` | 只重写本场对白 |
| `strengthen_conflict` | 加强戏剧冲突 |
| `short_drama_pace` | 改成短剧节奏 |
| `add_camera_hints` | 补充镜头提示 |
| `reduce_narration` | 减少旁白 |
| `adjust_character_voice` | 调整某个人物语气 |

AI 局部重写必须保持以下字段不变：

- `scene.id`
- `source_chapter`
- `int_ext`
- `time_of_day`
- `location`

服务端会把替换后的剧本重新走 `Screenplay.model_validate`，确保局部修改后整体 YAML 仍然可用。

## 14. 完整 YAML 示例

```yaml
schema_version: '1.0'
title: 低智商犯罪
genre: 悬疑 / 犯罪
adaptation_type: 短剧
logline: 方超和刘直试图用爆炸制造城市混乱，掩护一场黄金店抢劫。
source:
  chapter_count: 4
  chapter_titles:
    - 第一章
    - 第二章
    - 第三章
    - 第四章
global_state:
  characters:
    - id: character-1
      name: 方超
      aliases: []
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      traits:
        - 主导
        - 冒险
      goal: 完成黄金店抢劫并利用城市混乱脱身。
      arc: 从自信掌控行动到逐渐暴露计划漏洞。
      consistency_note: 保持方超主导计划、用调侃掩饰紧张的表达方式。
    - id: character-2
      name: 刘直
      aliases: []
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      traits:
        - 犹疑
      goal: 配合方超完成行动。
      arc: 从被动执行到因压力增加行动风险。
      consistency_note: 保持刘直跟随、迟疑和理解慢半拍的状态。
  locations:
    - id: location-1
      name: 酒店客房
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      description: 方超和刘直在行动前检查伪装和装备。
    - id: location-2
      name: 黄金店
      first_appearance: 第一章
      appearance_chapters:
        - 第一章
      description: 抢劫目标地点。
  timeline:
    - id: event-1
      order: 1
      chapter: 第一章
      time_marker: 下午
      summary: 方超和刘直完成行动前准备，并前往黄金店。
characters:
  - id: character-1
    name: 方超
    description: 抢劫计划的主导者。
    motivation: 用爆炸和交通混乱掩护抢劫。
    arc: 从自信掌控计划到逐渐暴露破绽。
  - id: character-2
    name: 刘直
    description: 方超的同伙。
    motivation: 跟随方超完成行动并分得收益。
    arc: 从被动执行到在压力中显露迟疑。
scenes:
  - id: scene-1
    heading: INT. 酒店客房 - DAY
    int_ext: INT.
    time_of_day: DAY
    location: 酒店客房
    source_chapter: 第一章
    summary: 方超和刘直在酒店客房里完成抢劫前的伪装和准备。
    goal: 方超要确认装备和路线，带刘直进入行动。
    conflict: 刘直跟不上方超的节奏，行动前的紧张和荒诞感被放大。
    beat: 方超从社会抱怨切入行动命令。
    subtext: 方超用玩笑和抱怨掩盖犯罪前的兴奋与自我合理化。
    characters:
      - character-1
      - character-2
    characters_present:
      - character-1
      - character-2
    props:
      - 手枪
      - 双肩包
      - 假发
      - 胶皮手套
    dramatization_decisions:
      - source_text: 方超手持一把枪，站在酒店客房的窗户边。
        target: action
        rendering: 方超撩开窗帘，枪压在掌心下，观察街对面的店铺。
        reason: 可见行为直接建立危险状态和行动目标。
      - source_text: “洗脚的又怎么了？”
        target: dialogue
        rendering: 洗脚的又怎么了？
        reason: 原文明确对白保留为台词，用于表现人物关系和节奏反差。
      - source_text: 方超抱怨社会浮躁、到处都是陷阱和骗局。
        target: subtext
        rendering: 方超把即将实施的抢劫包装成对混乱社会的反击。
        reason: 心理判断和价值判断不直接搬运，转为潜台词。
    elements:
      - type: action
        text: 方超站在窗边，撩开窗帘，枪口被他压在掌心下。
      - type: dialogue
        character: character-1
        parenthetical: 盯着窗外
        emotion: 讥讽
        text: 你有没有感觉，现在的人普遍浮躁？
      - type: dialogue
        character: character-2
        parenthetical: 停下整理背包
        emotion: 困惑
        text: 洗脚的又怎么了？
      - type: action
        text: 方超检查假发和胡子，把手枪锁上保险，藏到腰后。
    camera_hints:
      - 近景：方超撩开窗帘观察街对面。
      - 特写：手枪保险被扣上。
```

## 15. 校验规则

基础结构：

- `schema_version` 必须是 `1.0`。
- `title`、`logline` 不能为空。
- `genre` 必填，但允许为空字符串。
- `adaptation_type` 必须是五种改编类型之一。
- `source.chapter_count >= 3`。
- `source.chapter_titles` 至少 3 项。
- `scenes` 至少 1 项。

引用关系：

- 顶层 `characters[].id` 不能重复。
- `global_state.characters[].id` 必须存在于顶层 `characters`。
- `scene.characters`、`scene.characters_present`、`dialogue.character` 必须引用已存在角色。
- `scene.source_chapter` 必须存在于 `source.chapter_titles`。
- `global_state` 中人物、地点和时间线引用的章节必须存在。

场景结构：

- `scene.id` 必须符合 `scene-数字`。
- `heading` 必须以 `int_ext` 开头、包含 `location`、以 `time_of_day` 结尾。
- `int_ext` 必须是 `INT.` 或 `EXT.`。
- `time_of_day` 必须是 `DAY` 或 `NIGHT`。
- `goal`、`conflict`、`beat`、`subtext` 不能为空。
- `dramatization_decisions` 至少 1 项。
- `elements` 至少 1 项。
- `props`、`camera_hints` 必须是字符串列表。

元素结构：

- `action.text` 不能为空。
- `dialogue.character` 必须引用已存在角色。
- `dialogue.text` 不能为空。
- `dialogue.parenthetical` 和 `dialogue.emotion` 可以为空字符串。

## 16. 为什么选择 YAML

YAML 适合作为 Story2Script 的中间格式：

- 比 JSON 更适合人工阅读和编辑。
- 比 Markdown 更稳定，能保留字段、列表和引用关系。
- 可以被程序解析回 `Screenplay` 对象。
- 适合前端编辑、后端校验、局部重生成和后续导出。

纯文本剧本难以可靠区分场景、动作、对白和人物引用；YAML 则能把这些信息结构化保留下来。
