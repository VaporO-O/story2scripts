# Story2Script YAML Schema v1.0 设计说明

## 1. 设计目标

Story2Script 的 YAML Schema 用于描述“由小说改编而来的剧本初稿”。它不是最终拍摄剧本格式，
而是一个适合 AI 生成、作者编辑、程序校验和后续导出的中间结构。

该 Schema 主要解决四个问题：

1. **满足赛题要求**：能将三章以上小说转换为结构化 YAML 剧本。
2. **便于作者编辑**：YAML 比 JSON 更适合人工阅读和手动修改。
3. **便于程序校验**：字段、类型、章节数量、角色引用都可以被检查。
4. **保留改编追溯**：每个场景都记录来源章节，方便作者回看原文。

机器可读 Schema 位于：

```text
schema/screenplay.schema.json
```

应用中的 Pydantic 模型位于：

```text
src/story2script/screenplay.py
```

## 2. 顶层结构

完整 YAML 剧本包含以下顶层字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 当前固定为 `1.0`，用于未来兼容升级 |
| `title` | string | 是 | 剧本标题 |
| `genre` | string | 是 | 剧本类型，例如悬疑、剧情、科幻 |
| `logline` | string | 是 | 一句话故事梗概 |
| `source` | object | 是 | 原小说来源章节信息 |
| `characters` | array | 是 | 全局角色表 |
| `scenes` | array | 是 | 剧本场景列表 |

设计原因：

- `schema_version` 让未来升级 Schema 时可以做版本迁移。
- `source` 让剧本内容能追溯到原小说章节。
- `characters` 放在顶层，方便对白和场景通过稳定 ID 引用角色。
- `scenes` 是剧本主体，按叙事顺序排列。

## 3. Source：来源章节信息

示例：

```yaml
source:
  chapter_count: 3
  chapter_titles:
    - 第一章 雾中的信
    - 第二章 旧钟楼
    - 第三章 潮汐之前
```

字段说明：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `chapter_count` | integer | 大于等于 3 | 原小说有效章节数量 |
| `chapter_titles` | array[string] | 至少 3 项 | 原小说章节标题列表 |

设计原因：

- 赛题明确要求处理三章以上小说，因此 `chapter_count >= 3` 是硬性约束。
- `chapter_titles` 用于让场景的 `source_chapter` 可以指回原章节。
- 应用层额外校验 `chapter_count` 必须等于 `chapter_titles` 的数量，避免来源信息不一致。

## 4. Character：角色表

示例：

```yaml
characters:
  - id: character-1
    name: 林夏
    description: 寻找父亲失踪真相的年轻记者
    motivation: 在潮汐到来前找到答案
```

字段说明：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 小写字母、数字、下划线、连字符 | 角色稳定标识 |
| `name` | string | 非空 | 角色名称 |
| `description` | string | 可为空 | 角色简介 |
| `motivation` | string | 可为空 | 角色动机 |

设计原因：

- 使用 `id` 而不是角色姓名做引用，是为了避免角色改名、别名、昵称导致引用失效。
- `description` 和 `motivation` 是作者打磨剧本时最常用的角色信息，保留在 v1.0 中。
- 角色表放在顶层，便于后续扩展人物关系图、角色弧光分析和前端角色管理。

## 5. Scene：场景

示例：

```yaml
scenes:
  - id: scene-1
    heading: EXT. 雾港码头 - DAWN
    source_chapter: 第一章 雾中的信
    summary: 林夏在码头发现匿名信。
    characters:
      - character-1
    elements:
      - type: action
        text: 雾气盖住码头，海水拍打锈蚀的系船柱。
      - type: dialogue
        character: character-1
        parenthetical: 低声
        text: 这不可能是巧合。
```

字段说明：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | string | `scene-数字` | 场景稳定标识 |
| `heading` | string | 非空 | 场景标题 |
| `source_chapter` | string | 必须来自 `source.chapter_titles` | 来源章节 |
| `summary` | string | 非空 | 场景摘要 |
| `characters` | array[string] | 角色 ID 列表 | 本场出现的角色 |
| `elements` | array | 至少 1 项 | 场景内动作和对白 |

设计原因：

- `heading` 使用类似专业剧本的格式，例如 `INT. 房间 - NIGHT`，便于未来导出专业剧本格式。
- `source_chapter` 解决 AI 改编结果难追溯的问题，作者可以快速回到原文核对。
- `summary` 便于前端做场景列表、搜索和节拍检查。
- `elements` 保留场景内顺序，动作和对白可以交替出现。

## 6. Scene Element：动作与对白

### Action

示例：

```yaml
- type: action
  text: 林夏推开钟楼木门，墙上的旧钟停在十年前。
```

字段说明：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 固定为 `action` | 元素类型 |
| `text` | string | 非空 | 可见或可听见的动作描述 |

### Dialogue

示例：

```yaml
- type: dialogue
  character: character-1
  parenthetical: 低声
  text: 这不可能是巧合。
```

字段说明：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 固定为 `dialogue` | 元素类型 |
| `character` | string | 必须引用已有角色 ID | 说话角色 |
| `parenthetical` | string | 可为空 | 语气、动作或状态提示 |
| `text` | string | 非空 | 对白内容 |

设计原因：

- 使用 `type` 区分动作和对白，前端可以据此渲染不同编辑控件。
- `dialogue.character` 使用角色 ID，保证对白和角色表之间存在稳定关系。
- `parenthetical` 保留剧本中常见的语气提示，但不强制填写。

## 7. 完整 YAML 示例

```yaml
schema_version: '1.0'
title: 雾港来信
genre: 悬疑 / 剧情
logline: 围绕《雾港来信》核心冲突展开的剧本初稿。
source:
  chapter_count: 3
  chapter_titles:
    - 第一章 雾中的信
    - 第二章 旧钟楼
    - 第三章 潮汐之前
characters:
  - id: character-1
    name: 林夏
    description: 从原文对白中自动识别的角色。
    motivation: 待作者进一步补充。
scenes:
  - id: scene-1
    heading: INT. 第一章 雾中的信 - DAY
    source_chapter: 第一章 雾中的信
    summary: 凌晨五点，林夏在码头捡到一封没有署名的信。
    characters:
      - character-1
    elements:
      - type: action
        text: 凌晨五点，林夏在码头捡到一封没有署名的信。
      - type: dialogue
        character: character-1
        parenthetical: ''
        text: 这不可能是巧合。
```

## 8. 校验规则

当前系统同时使用 JSON Schema 文件和 Pydantic 模型进行校验。

基础结构校验：

- `schema_version` 必须为 `1.0`
- `title`、`logline`、`heading`、`summary` 等核心文本不能为空
- `source.chapter_count >= 3`
- `source.chapter_titles` 至少包含 3 项
- `scenes` 至少包含 1 个场景
- 每个场景的 `elements` 至少包含 1 项
- 角色 ID 只能包含小写字母、数字、下划线和连字符
- 场景 ID 必须符合 `scene-数字` 格式

应用层引用校验：

- `source.chapter_count` 必须等于 `source.chapter_titles` 数量
- 角色 ID 不能重复
- 场景 ID 不能重复
- `scene.source_chapter` 必须存在于 `source.chapter_titles`
- 场景角色列表中的 ID 必须存在于 `characters`
- 对白中的 `character` 必须存在于 `characters`

## 9. 为什么选择 YAML

选择 YAML 而不是 Markdown 或纯文本，是因为：

- YAML 适合人工阅读和编辑。
- YAML 可以被程序解析为结构化对象。
- YAML 能保留列表、对象、字段名和层级关系。
- YAML 比纯剧本文本更适合后续做校验、编辑器、导出和 AI 迭代。

Markdown 更适合展示，不适合作为稳定数据交换格式；纯文本剧本则难以可靠区分场景、动作、对白和角色。

## 10. 扩展方向

v1.0 暂时聚焦“剧本初稿”，没有加入过多拍摄层信息。后续可以在不破坏主体结构的前提下扩展：

- `beats`：剧情节拍
- `locations`：地点表
- `props`：关键道具
- `relationships`：人物关系
- `estimated_duration`：场景预计时长
- `shots`：镜头拆分
- `revision_notes`：作者修改意见

这些内容适合在基础剧本生成稳定后逐步加入，避免第一版 Schema 过重，增加 AI 输出难度。

