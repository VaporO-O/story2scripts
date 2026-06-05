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
- 角色表
- 场景列表
- 动作与对白元素
- 场景、角色、来源章节之间的引用关系

### 离线演示转换器

当前代码中提供 `DemoConverter`，可在不配置 AI API Key 的情况下，将已识别章节转换为符合
Schema 的剧本对象。

转换器会：

- 将每个章节映射为一个初始场景
- 从章节中的引号内容抽取一条对白
- 根据“某某说/问/喊”格式识别说话人
- 生成符合 `Screenplay` 模型的结构化结果

### 小说转剧本接口

接口：`POST /api/convert`

该接口会解析三章以上小说文本，并使用离线演示转换器返回结构化剧本 JSON。

请求示例：

```json
{
  "title": "雾港来信",
  "genre": "悬疑",
  "novel_text": "第一章 开始\n林夏说：“出发吧。”\n第二章 转折\n雨落下来。\n第三章 结局\n太阳升起。"
}
```

响应中同时包含：

- `screenplay`：结构化剧本 JSON
- `yaml_text`：可编辑、可保存的 YAML 剧本初稿
- `mode`：当前转换模式

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

### 示例小说

接口：`GET /api/examples/novel`

该接口返回内置的三章示例小说、标题和类型。后续 Web 页面可用它实现“填入示例”，也可以用于
快速测试 `/api/convert`。

## 测试

```bash
pytest
ruff check .
```

## Web 工作台

项目首页 `http://127.0.0.1:8000` 提供左右对照工作台：

- 左侧输入或填入示例小说
- 右侧展示生成后的 YAML 剧本
- 支持 YAML 校验
- 支持下载 `screenplay.yaml`

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

当前版本使用规则提取，适合本地演示和稳定测试；后续可在不改变接口结构的前提下接入 AI 增强。

## 心理描写外化

转换器会尝试识别“觉得、意识到、发冷、不是意外”等心理描写，并将其外化为：

- 可拍摄的动作
- 带 `emotion` 的对白
- `camera_hints` 镜头提示

例如“林澈突然觉得背后一阵发冷，他隐约意识到，姐姐的失踪可能不是意外。”会被转换为停步回头、
紧张对白和近景提示。

## 转换器模式

`POST /api/convert` 支持 `mode` 字段，用于选择转换器实现。

当前支持：

- `demo`：本地规则转换器，不需要外部 API Key

后续会扩展：

- `ai`：调用外部 LLM，智能识别心理描写、场景改编和人物信息

### 外部 LLM 配置

项目已预留 OpenAI-compatible Chat Completions 接口。你确定服务商后，参考 `.env.example` 填写：

```bash
CONVERTER_MODE=ai
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

如果暂时没有配置 API Key，继续使用默认 `demo` 模式即可。
## Schema 文档

YAML Schema 设计说明见：

```text
docs/YAML_SCHEMA.md
```
