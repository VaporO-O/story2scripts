# Story2Script

Story2Script 是一款 AI 辅助小说转剧本工具，目标是将三章以上的小说文本转换为结构化、
可编辑的 YAML 剧本初稿。

当前 `main` 分支是项目的最小可运行基础版本。后续功能将通过单一职责的 Pull Request
逐步加入，确保每次合并后项目均可运行。

## 本地运行

要求 Python 3.11 或更高版本。

```bash
pip install -e ".[dev]"
uvicorn story2script.main:app --reload
```

打开：

- 项目首页：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

## 已支持功能

### 章节识别

接口：`POST /api/chapters/preview`

支持识别中文章节标题和英文章节标题：

- `第一章 雾中的信`
- `第1章 雾中的信`
- `Chapter 1 The Letter`

请求示例：

```json
{
  "novel_text": "第一章 开始\n内容一\n第二章 转折\n内容二\n第三章 结局\n内容三"
}
```

当识别出的有效章节少于 3 个时，接口会返回校验错误。

### 剧本 Schema

接口：`GET /api/screenplay/schema`

该接口返回 Story2Script 剧本结构的 JSON Schema，用于约束后续 AI 生成结果和前端编辑数据。
当前 Schema 覆盖：

- 原小说来源信息
- 跨章节一致性状态表
- 角色表
- 场景列表
- 内外景、拍摄时段、地点、出场人物和道具等可制作性字段
- 叙述到动作、对白、潜台词和场景描述的戏剧化分类决策
- 动作与对白元素
- 场景、角色、来源章节之间的引用关系

### 跨章节一致性状态表

接口：`POST /api/consistency/global-state`

该接口会先扫描三章以上小说，生成一张固定的全局状态表：

- `characters`：人物表，记录稳定角色 ID、首次出场、出现章节、性格、目标和人物弧光
- `locations`：地点表，记录地点首次出现和跨章节出现情况
- `timeline`：时间线，按章节顺序记录时间标记和事件摘要

长文本接入 LLM 时，系统会先抽取这张表，再把它作为固定上下文传给分块转换和局部重写，避免同一人物在
第 1 章和第 3 章出现时姓名、性格、目标或弧光发生漂移。

请求示例：

```json
{
  "novel_text": "第一章 雾起\n清晨，林夏在码头等待。林夏说：“我会查下去。”\n第二章 旧楼\n林夏来到旧钟楼。\n第三章 潮汐\n夜里，林夏回到码头。"
}
```

### 离线演示转换器

当前代码中提供 `DemoConverter`，可在不配置 AI API Key 的情况下，将已识别章节转换为符合
Schema 的剧本对象。

转换器会：

- 先生成 `global_state`，把人物表、地点表和时间线作为跨章节固定上下文
- 根据时间变化、地点变化、人物进出、情节转折和冲突变化拆分场景
- 为每个场景生成 `int_ext`、`time_of_day`、`location`、`characters_present` 和 `props`，
  对齐工业级剧本格式中的 slug line、出场人物和道具拆解
- 为每个场景生成 `dramatization_decisions`，显式判断小说叙述应改写成动作行、对白、潜台词还是场景描述
- 从章节中的引号内容抽取一条对白
- 根据“某某说/问/喊”格式识别说话人
- 按改编类型生成不同的动作、冲突、节拍和生产提示
- 生成符合 `Screenplay` 模型的结构化结果

### 小说转剧本接口

接口：`POST /api/convert`

该接口会解析三章以上小说文本，并使用离线演示转换器返回结构化剧本 JSON。

请求示例：

```json
{
  "title": "雾港来信",
  "genre": "悬疑",
  "adaptation_type": "影视剧",
  "novel_text": "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"
}
```

响应中同时包含：

- `screenplay`：结构化剧本 JSON
- `screenplay.global_state`：跨章节人物表、地点表和时间线
- `screenplay.scenes[].int_ext/time_of_day/location/characters_present/props`：可制作性场景字段
- `screenplay.scenes[].dramatization_decisions`：叙述到戏剧表达的分类决策
- `yaml_text`：可编辑、可保存的 YAML 剧本初稿
- `mode`：当前转换模式
- `adaptation_type`：当前改编类型

### 改编类型选择

`POST /api/convert` 支持 `adaptation_type` 字段，用于选择同一段小说的改编方向。默认值为 `影视剧`。

| 改编类型 | 输出特点 |
| --- | --- |
| `短剧` | 节奏快，冲突密集，强反转 |
| `影视剧` | 场景完整，镜头感强 |
| `舞台剧` | 舞台提示、人物走位更多 |
| `广播剧` | 音效、旁白、声音表演更多 |
| `分镜脚本` | 镜头、画面、景别更多 |

### YAML 输出

项目使用 `PyYAML` 将 `Screenplay` 模型序列化为 YAML。导出的 YAML 会保留中文内容和字段顺序，
便于作者直接阅读、编辑和保存。

### YAML 校验

接口：`POST /api/yaml/validate`

该接口用于校验编辑后的 YAML 是否仍符合 Story2Script 剧本 Schema。

请求示例：

```json
{
  "yaml_text": "schema_version: '1.0'\ntitle: 示例剧本\n..."
}
```

校验通过时返回 `valid: true`；YAML 格式错误或字段不符合 Schema 时返回 `422`。

### 局部重生成

接口：`POST /api/scenes/rewrite`

该接口用于在已有 YAML 剧本上只重写某一个场景，避免每次修改都重新生成全文。请求需要传入当前
`yaml_text`、目标 `scene_id` 和局部操作 `operation`，接口会返回更新后的结构化剧本和 YAML。
默认使用本地规则模式；配置外部 LLM 后，可以传入 `mode: "ai"` 使用 AI 局部重写。

当前支持的操作：

| 操作 | 说明 |
| --- | --- |
| `rewrite_dialogue` | 重新生成本场对白 |
| `strengthen_conflict` | 加强戏剧冲突 |
| `short_drama_pace` | 改成短剧节奏 |
| `add_camera_hints` | 增加镜头提示 |
| `reduce_narration` | 减少旁白 |
| `adjust_character_voice` | 调整某个人物的语气 |

请求示例：

```json
{
  "yaml_text": "schema_version: '1.0'\n...",
  "scene_id": "scene-1",
  "operation": "strengthen_conflict",
  "mode": "demo"
}
```

调整人物语气时可以额外传入：

```json
{
  "yaml_text": "schema_version: '1.0'\n...",
  "scene_id": "scene-1",
  "operation": "adjust_character_voice",
  "mode": "ai",
  "character_id": "character-1",
  "tone": "更锋利"
}
```

AI 局部重写复用项目根目录 `.env` 中的 OpenAI-compatible 配置：

```bash
AI_API_KEY=your-api-key
AI_BASE_URL=https://your-provider.example/v1
AI_MODEL=your-model-name
AI_TIMEOUT_SECONDS=120
```

AI 模式只要求模型返回目标 `scene` 的 JSON，不返回完整剧本。服务端会校验 `scene.id` 和
`source_chapter` 不变，再把该 scene 替换回原剧本，并重新通过 `Screenplay` 模型校验，保证 YAML
结构仍然有效。局部重写的 AI prompt 也会携带 `global_state`，因此只改某一场时仍会遵守跨章节人物、
地点和时间线约束。

### 示例小说

接口：`GET /api/examples/novel`

该接口返回内置示例小说（节选自《低智商犯罪》，含四章）、标题和类型。后续 Web 页面可用它实现
“填入示例”，也可以用于快速测试 `/api/convert`。

## 测试

```bash
python -m pytest
python -m ruff check .
python -m compileall -q src tests
```

## Web 工作台

项目首页 `http://127.0.0.1:8000` 提供左右对照工作台：

- 左侧输入或填入示例小说
- 支持导入 `.epub`、`.txt`、`.md`、`.markdown`、`.csv`、`.log` 等小说文件
- 对 `.mobi`、`.azw`、`.azw3` 给出明确提示，建议先转换为 EPUB 或 TXT
- 支持选择改编类型
- 支持在生成按钮旁切换本地 / AI 全文转换模式
- 右侧展示生成后的 YAML 剧本
- 支持 YAML 校验
- 支持下载 `screenplay.yaml`
- 支持展示人物小传表
- 生成的 YAML 内包含 `global_state`，可展示人物、地点和时间线如何保持跨章节一致
- 支持对指定场景进行局部重生成，包括对白、冲突、短剧节奏、镜头提示、旁白和人物语气

## 人物小传提取

接口：`POST /api/characters/profiles`

该接口会从三章以上小说中自动提取人物小传信息，返回字段包括：

- `name`：人物姓名
- `role`：角色定位
- `personality`：性格
- `goal`：目标
- `relationships`：与他人的关系
- `appearance_chapters`：出场章节
- `key_change`：关键变化

接口支持 `mode` 字段，用于在本地规则与 AI 增强之间切换：

| mode | 说明 |
| --- | --- |
| `demo` | 默认。纯本地规则提取，无需 API Key，稳定可复现 |
| `ai` | 调用外部 LLM，理解原文并补全性格、目标、人物关系和关键变化 |

请求示例：

```json
{
  "mode": "ai",
  "novel_text": "第一章 ...\n第二章 ...\n第三章 ..."
}
```

### 为什么需要 AI 增强

本地规则只能做精确的模板匹配，无法“理解”原文：`personality` 仅命中固定的性格关键词、
`goal` 只截取含触发词的句子、`key_change` 需要字面的“从 X 到 Y”、`relationships` 需要字面的
“X 是 Y 的弟弟”。自然行文很少正好用这些模板，因此这些字段经常落回“待作者进一步补充”。
性格、动机、人物弧光与人物关系本质上需要阅读理解，正是 LLM 擅长、硬规则做不到的部分。

`ai` 模式采用“本地定名单、LLM 补语义”的混合策略，复用与 `/api/convert` 相同的
OpenAI-compatible 配置（`AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` / `AI_TIMEOUT_SECONDS`）：

- 人物名单与 `appearance_chapters` 仍由本地规则确定，**LLM 不能新增、漏掉或改写人物**，
  从而继承上面稳定的人名识别结果
- LLM 只负责补全 `role`、`personality`、`goal`、`relationships`、`key_change`
- 服务端逐条按 `CharacterProfile` Schema 校验并以本地结果兜底：LLM 留空或给占位文字的
  字段会自动回退到本地值；模型返回非法 JSON、缺少 `profiles` 列表等硬错误才返回 `422`

没有配置 API Key 时继续使用默认 `demo` 模式即可。

### 本地人名识别

人名识别采用“姓氏闸门 + 多重证据”策略，确保稳定可复现：

- 候选词必须以常见姓氏开头，并排除虚词、介词、地点/机构词尾与动词词尾，从根本上
  避免把“直接 / 点头 / 马上 / 皱眉 / 幽幽地 / 继续”等动词、副词误判成人名
- 通过长度消歧与片段抑制，区分“张一昂”与“张一”、剔除“查周荣”这类“动词 + 人名”组合
- 结合“说话人引号、人物称谓（吴主任 / 陈法医）、主语谓语、被指向动词”等证据与词频，
  把真实人物与“时间 / 任务 / 能力 / 关系”等常见词区分开
- 同一套已识别人物名单会复用到离线转换器的台词归属上，避免转换阶段再次把垃圾词
  注册成新人物，导致台词与人物关系混乱

规则识别覆盖绝大多数常见情形；对于姓名与常见词同形等长尾歧义，可进一步交由可选的 AI
增强路径处理。

## 心理描写外化

转换器会尝试识别心理描写，并将其外化为：

- 可拍摄的动作
- 潜台词和人物反应
- `camera_hints` 镜头提示

例如“林澈突然觉得背后一阵发冷，他隐约意识到，姐姐的失踪可能不是意外。”会被转换为停步回头、
潜台词压力和近景提示，而不是把心理判断机械搬成一句台词。

## 叙述到戏剧化改写

每个场景会生成 `dramatization_decisions`，记录系统如何判断小说叙述的改写方向：

| target | 改写方向 |
| --- | --- |
| `action` | 角色可见行为、身体反应、场面推进 |
| `dialogue` | 原文明确对白，或需要通过信息交换推动冲突 |
| `subtext` | 心理活动、情绪判断、未说出口的意图 |
| `scene_description` | 天气、空间、背景和氛围 |

AI 转换 prompt 会显式要求模型做这个分类决策，避免把小说里的心理描写、环境叙述和背景说明直接搬进剧本。

`dramatization_decisions` 作为“原文 → 改写方向”的分类记录保留在结构化数据中；可读剧本预览
只展示这层分类分析，不再展示方法论式的说明文字（reason），避免说明性内容混进剧本阅读视图。
台词说话人识别同时支持前置与后置写法（“方超说：…” 与 “…”方超握紧手枪），开场对白不会被
误当成动作行；场景 `location` 会排除人物名等非地点候选，避免出现“EXT. 刘直 - DAY”这类错误。

## 转换器模式

`POST /api/convert` 支持 `mode` 字段，用于选择转换器实现。

当前支持：

- `demo`：本地规则转换器，不需要外部 API Key
- `ai`：调用外部 LLM，智能识别心理描写、场景改编和人物信息

### 外部 LLM 配置

项目已预留 OpenAI-compatible Chat Completions 接口。你确定服务商后，在项目根目录创建正式 `.env`
配置文件：

```bash
AI_API_KEY=your-api-key
AI_BASE_URL=https://your-provider.example/v1
AI_MODEL=your-model-name
AI_TIMEOUT_SECONDS=120
```

随后调用 `/api/convert` 时传入：

```json
{
  "mode": "ai",
  "title": "作品标题",
  "genre": "悬疑",
  "novel_text": "第一章 ...\n第二章 ...\n第三章 ..."
}
```

Web 工作台默认使用本地模式，因此没有 API Key 也能演示；配置上述环境变量后，在生成按钮旁选择 `AI`
即可展示 LLM 改编质量。API 调用中如果暂时没有配置 API Key，继续使用默认 `demo` 模式即可。
AI 转换会先在本地抽取 `global_state`，并将其写入 prompt；服务端会用这份固定状态表回填并校验最终
`Screenplay`，确保分块改编不会丢失跨章节一致性。

启动服务时，统一 LLM 客户端会自动读取项目根目录 `.env`。如果同名配置同时存在于系统环境变量和
`.env` 中，系统环境变量优先。`.env` 已被 git 忽略，避免真实 API Key 被提交到仓库。

AI 全文转换遵循固定校验链路：

```text
llm_json -> 容错解析 -> normalize -> Screenplay.model_validate -> screenplay_to_yaml
```

LLM 只负责生成 JSON；服务端会先做一层归一化修复，再用 `Screenplay` Schema 做最终校验，校验通过后
才会导出 YAML。容错解析会先尝试直接解析，再自动剥离 Markdown ``` 代码块围栏、忽略 JSON 前后的
说明文字，最后回退到提取最外层 `{...}`，避免模型把合法 JSON 包在围栏或解释里时被误判为“非法 JSON”。
（全文转换、局部重写、人物小传 AI 增强共用 `src/story2script/llm_client.py` 的 `loads_json_object`。）
归一化会自动修复 LLM 常见的小偏差，避免动辄整篇失败：

- 回填或修正缺失 / 格式不对的 `scene.id`（统一为 `scene-数字`）
- 把中文或不规范的 `int_ext`、`time_of_day` 归一为 `INT./EXT.` 与 `DAY/NIGHT`，并据此重建 `heading`
- 丢弃 `scene`、`element`、`dramatization_decisions` 上 Schema 不允许的多余字段
- 把用人物名引用的对白映射回稳定的角色 `id`，无法解析的对白降级为动作行
- 缺失的 `goal`、`conflict`、`beat`、`subtext`、`elements`、`dramatization_decisions` 等会补成
  可编辑的占位内容，并按请求回填缺失的 `adaptation_type`

只有真正无法恢复的情况才会返回清晰的 `422` 错误，不会把坏 YAML 返回给前端，包括：模型返回非法
JSON、没有任何有效场景、或显式给出与请求矛盾的 `adaptation_type`。

全文转换和局部重写共用 `src/story2script/llm_client.py` 中的统一 LLM 客户端。该客户端自动读取
`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 和 `AI_TIMEOUT_SECONDS`，调用 `/chat/completions`，
并统一处理超时、网络错误、HTTP 错误和模型空响应。

## Schema 文档

YAML Schema 设计说明见：

```text
docs/YAML_SCHEMA.md
```
