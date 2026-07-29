# Story2Script

Story2Script 是一个“小说章节 -> 结构化剧本 YAML”的改编工作台。它面向三章以上的长文本小说，先识别章节、人物、地点和时间线，再把小说叙述改写成可预览、可编辑、可校验的剧本初稿。

项目当前内置示例为《低智商犯罪》（悬疑 / 犯罪）。文档和演示都以这个示例为准。

## 快速运行

要求 Python 3.11 或更高版本。

```bash
pip install -e ".[dev]"
python -m uvicorn story2script.main:app --reload
```

打开：

- 工作台：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

如果 Windows 上 `8000` 端口被占用或被系统权限拦截，可以换端口：

```bash
python -m uvicorn story2script.main:app --reload --port 8001
```
## Demo演示

- 健康检查：<https://www.bilibili.com/video/BV1ZyE86bEAY/>

## 工作台能力

Web 工作台支持完整演示链路：

- 粘贴小说文本，或填入内置示例《低智商犯罪》。
- 导入 `.txt`、`.text`、`.md`、`.markdown`、`.csv`、`.log`、`.epub` 文件。
- 对 `.mobi`、`.azw`、`.azw3` 给出明确提示，建议先转换成 EPUB 或 TXT。
- 选择改编类型：短剧、影视剧、舞台剧、广播剧、分镜脚本。
- 在生成按钮附近切换本地 / AI 模式。
- 显示转换进度条、当前阶段和错误信息。
- 右侧提供分场预览和 YAML 源码视图。
- 支持 YAML 校验、下载 `screenplay.yaml`。
- 支持人物小传提取。
- 支持局部重生成：重新生成本场对白、加强戏剧冲突、改成短剧节奏、增加镜头提示、减少旁白、调整人物语气。
- 支持机审打分与逐场景人审，审校报告可下载。
- 「改编 Agent」面板：设定目标后一键启动自主改编代理，实时进度、逐步决策轨迹、前后分数对比，可保存 / 载入历史会话。

## 核心设计

### 剧本不是小说搬运

Story2Script 的核心原则是“叙述 -> 戏剧化”。小说可以依靠心理描写和叙述推进，剧本必须依靠可见动作、冲突、对白、场面调度和潜台词推进。

因此每个场景都会显式建模：

- `goal`：角色在本场的可见目标。
- `conflict`：阻挡目标的戏剧冲突。
- `beat`：节拍或转折点。
- `subtext`：动作和对白之下未说出口的压力。
- `dramatization_decisions`：系统如何判断原文叙述应改写成动作、对白、潜台词或场景描述。

### 对齐工业级剧本格式

场景包含可制作性字段：

- `heading`：类似 `INT. 酒店客房 - DAY` 的 slug line。
- `int_ext`：`INT.` 或 `EXT.`。
- `time_of_day`：`DAY` 或 `NIGHT`。
- `location`：可拍摄地点。
- `characters_present`：本场实际在场人物。
- `props`：可制作道具，如手枪、双肩包、塑料面具、停业装修贴纸。

这些字段让 YAML 不只是文本结果，而是能继续服务于场景表、道具表、分镜和预算拆解的中间结构。

### 跨章节一致性

长篇小说无法一次完整塞给 LLM，分块转换又容易出现人物名字不一致、性格漂移、时间线前后矛盾。Story2Script 会先扫描全文，生成 `global_state`：

- `characters`：人物表，包含稳定角色 ID、出场章节、性格、目标和人物弧光。
- `locations`：地点表，记录地点首次出现和跨章节出现情况。
- `timeline`：章节级事件时间线。

全文转换和局部重生成都会携带这张状态表，确保例如第 1 章出现的方超和第 3 章继续出现的方超引用同一个 `character-1`，人物目标和语气不会随分块漂移。

## 转换模式

### 本地模式

默认 `mode: "demo"`，无需 API Key，适合比赛现场演示和离线验收。本地转换器会：

- 解析三章以上小说。
- 抽取全局人物、地点、时间线。
- 按时间变化、地点变化、人物进出、情节转折、冲突变化拆分 scene。
- 识别前置和后置说话人，例如“方超说：……”和“……”方超转身。
- 生成符合 `Screenplay` Schema 的结构化剧本。

### AI 模式

`mode: "ai"` 使用 OpenAI-compatible Chat Completions API。配置写在项目根目录正式 `.env` 中，系统会自动读取：

```bash
AI_API_KEY=your-api-key
AI_BASE_URL=https://your-provider.example/v1
AI_MODEL=your-model-name
AI_TIMEOUT_SECONDS=120
AI_MAX_TOKENS=8192
AI_MAX_CONCURRENCY=4
AI_REVIEW_THRESHOLD=7.0
AI_REVIEW_MAX_ROUNDS=2
```

说明：

- `.env` 已被 git 忽略，不要提交真实 API Key。
- 系统环境变量优先级高于 `.env`。
- `AI_MAX_TOKENS` 可选；当模型输出被截断时可以调高，例如 `16384`。
- `AI_MAX_CONCURRENCY` 控制分块转换与审校的并发请求数，默认 `4`。
- `AI_REVIEW_THRESHOLD` / `AI_REVIEW_MAX_ROUNDS` 控制机审及格线（0-10）与自动修正轮次上限。
- 没有 API Key 时继续使用本地模式即可演示。

AI 全文转换流程：

```text
novel_text
-> parse_chapters
-> extract_global_story_state
-> chapter/chunk LLM calls
-> json.loads / tolerant JSON parsing
-> normalize scenes
-> Screenplay.model_validate
-> screenplay_to_yaml
```

LLM 只负责生成 JSON；服务端负责归一化、校验和导出 YAML。校验失败时返回清晰 `422` 错误，不会把坏 YAML 给前端。

## API 概览

| 接口 | 作用 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/examples/novel` | 获取内置示例《低智商犯罪》 |
| `POST /api/novels/import` | 导入文本或 EPUB 内容 |
| `POST /api/chapters/preview` | 预览章节识别结果 |
| `POST /api/characters/profiles` | 提取人物小传，支持本地 / AI 模式 |
| `POST /api/consistency/global-state` | 生成跨章节一致性状态表 |
| `GET /api/screenplay/schema` | 获取 Screenplay JSON Schema |
| `POST /api/convert` | 同步全文转换 |
| `POST /api/convert/jobs` | 启动带进度的异步转换 |
| `GET /api/convert/jobs/{job_id}` | 查询转换进度和结果 |
| `POST /api/yaml/validate` | 校验编辑后的 YAML |
| `POST /api/scenes/rewrite` | 局部重生成指定场景 |
| `POST /api/scenes/review` | 机审场景质量，支持自动修正与指定场景子集 |
| `POST /api/review/report/merge` | 把人审结论合并进审校报告 |
| `POST /api/agent/runs` | 启动自主改编 Agent 任务（异步，带进度） |
| `GET /api/agent/runs/{job_id}` | 查询 Agent 任务进度、决策轨迹与结果 |
| `GET /api/agent/sessions` | 列出已持久化的 Agent 会话 |
| `GET /api/agent/sessions/{session_id}` | 读取会话详情（剧本 YAML + 轨迹 + 报告） |

## 改编 Agent

在"固定管线"之上，项目内置一个**自主改编代理**（`story2script.agent`）：给定自然语言目标（如"让全部场景通过机审"），agent 以审校分数为目标函数，自主决定"审校 → 挑场景 → 局部重写 → 复评"的每一步，直到达标、无改进空间或预算耗尽。

```text
┌────────── AdaptationAgent 循环 ──────────┐
│ 规划 planner（受控 JSON 决策协议）        │
│   {"thought": "...",                     │
│    "action": {"tool": "...", "params"}}  │
│         ↓ 注册表校验                      │
│ 工具执行 AgentToolbox（6 个工具）         │
│   review / list_failing / get_scene      │
│   rewrite / compare_scores / finish      │
│         ↓ 紧凑观察值                      │
│ 记录 AgentStep → Scratchpad（短期记忆）   │
│         ↺ 直到 finish / 达标 / 步数上限   │
└──────────────────────────────────────────┘
```

四个核心模块对应 agent 基础能力：

| 能力 | 实现 |
| --- | --- |
| 任务规划 | 受控 JSON 决策协议：ai 模式由 LLM 每步产出决策；demo 模式用确定性规则策略走同一协议，无 API Key 可完整演示 |
| 工具调用 | `AgentToolbox` 注册表：参数校验、非法动作不抛异常而是作为观察值回喂 planner 自我修正，连续 3 次无效动作熔断 |
| 短期记忆 | `Scratchpad`：近几步保留全文、更早步骤压缩为一行，发给 planner 的历史总长度有界，上下文不随步数膨胀 |
| 长期记忆 | `AgentSessionStore`：目标、决策轨迹、指标与剧本成果持久化为 JSON 会话（`AGENT_SESSION_DIR`，默认 `.agent_sessions/`），可跨进程恢复 |

环境变量：`AGENT_MAX_STEPS`（单次运行步数上限，默认 12）、`AGENT_SESSION_DIR`（会话存储目录）；质量阈值复用 `AI_REVIEW_THRESHOLD`。

REST 用法：`POST /api/agent/runs` 提交 `{yaml_text, goal, mode, threshold, max_steps, save_session}`，轮询 `GET /api/agent/runs/{job_id}` 获取进度与逐步决策轨迹（thought / action / observation / 耗时）。MCP 用法：`run_adaptation_agent` 让 Claude 直接把某个 `screenplay_id` 交给代理接管，`load_agent_session` 恢复历史会话。Web 工作台的「04 改编 Agent」面板封装了同一流程：目标输入、实时进度条、决策轨迹时间线、前后分数对比与历史会话载入。

## MCP 服务

项目内置 [MCP](https://modelcontextprotocol.io)（Model Context Protocol）服务，可让 Claude Code、Claude Desktop 等 MCP 客户端直接驱动整个改编流程（stdio 传输）。

```bash
pip install -e .          # 安装依赖并注册 story2script-mcp 命令
python -m story2script.mcp_server --env-file /path/to/repo/.env   # 手动启动（可选）
```

注册到 Claude Code：

```bash
claude mcp add story2script -- python -m story2script.mcp_server --env-file D:/study/1/xi/.env
```

注册到 Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "story2script": {
      "command": "python",
      "args": ["-m", "story2script.mcp_server"],
      "env": {
        "PYTHONPATH": "D:/study/1/xi/src",
        "PYTHONUTF8": "1",
        "AI_API_KEY": "your-api-key",
        "AI_BASE_URL": "https://your-provider.example/v1",
        "AI_MODEL": "your-model-name"
      }
    }
  }
}
```

设计要点：服务端工作区持有小说与剧本状态，工具之间用短 ID（`novel-1` / `sp-1`）引用，避免客户端模型在每次调用里回显整份小说或 YAML；AI 配置既可用 `--env-file` 指向仓库 `.env`，也可写在客户端配置的 `env` 块中（后者优先）。

| MCP 工具 | 作用 |
| --- | --- |
| `get_example_novel` | 载入内置示例《低智商犯罪》，返回 `novel_id` |
| `import_novel_file` | 从本地路径导入 TXT/Markdown/CSV/LOG/EPUB 小说 |
| `preview_chapters` | 预览章节切分结果 |
| `convert_novel` | 转换为结构化剧本（demo/ai，带进度通知，可选转换后自动机审） |
| `list_scenes` / `get_scene` | 场景索引 / 单场景完整结构 |
| `rewrite_scene` | 六种局部重写操作，支持注入审校意见 |
| `review_screenplay` | 机审打分（四项标准），`auto_fix` 自动修正不达标场景 |
| `get_review_report` / `merge_human_review` | 完整审校报告 / 合并人审结论 |
| `load_screenplay` / `get_screenplay_yaml` / `save_screenplay` | YAML 载入 / 导出 / 落盘（可旁写审校报告） |
| `validate_yaml` | 校验剧本 YAML |
| `extract_character_profiles` | 提取人物小传（demo/ai） |
| `run_adaptation_agent` | 自主改编代理接管剧本：自主规划审校/重写/复评并返回决策轨迹 |
| `load_agent_session` | 恢复已持久化的 Agent 会话到工作区 |

另有资源 `screenplay://schema` 提供剧本 JSON Schema 全文。示例小说依赖仓库内 `examples/` 目录，请以源码方式（`pip install -e .` 或设置 `PYTHONPATH=src`）运行服务。

## 请求示例

### 全文转换

```json
{
  "title": "低智商犯罪",
  "genre": "悬疑 / 犯罪",
  "adaptation_type": "短剧",
  "mode": "demo",
  "novel_text": "第一章\n“你有没有感觉，现在的人普遍浮躁？”方超手持一把枪，站在酒店客房的窗户边。\n第二章\n刘直背上双肩包。\n第三章\n警车赶到现场。\n第四章\n张一昂开始复盘案件。"
}
```

响应包含：

- `screenplay`：结构化剧本 JSON。
- `yaml_text`：可编辑 YAML。
- `mode`：实际转换模式。
- `adaptation_type`：改编类型。

### 异步转换进度

```bash
curl -X POST http://127.0.0.1:8000/api/convert/jobs \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"低智商犯罪\",\"genre\":\"悬疑 / 犯罪\",\"adaptation_type\":\"短剧\",\"mode\":\"demo\",\"novel_text\":\"第一章...\"}"
```

返回 `job_id` 后轮询：

```bash
curl http://127.0.0.1:8000/api/convert/jobs/{job_id}
```

进度阶段包括章节解析、全局状态抽取、剧本生成、YAML 导出和完成状态。

### 局部重生成

```json
{
  "yaml_text": "schema_version: '1.0'\n...",
  "scene_id": "scene-1",
  "operation": "strengthen_conflict",
  "mode": "demo"
}
```

支持的 `operation`：

| operation | 说明 |
| --- | --- |
| `rewrite_dialogue` | 重新生成本场对白 |
| `strengthen_conflict` | 加强戏剧冲突 |
| `short_drama_pace` | 改成短剧节奏 |
| `add_camera_hints` | 增加镜头提示 |
| `reduce_narration` | 减少旁白 |
| `adjust_character_voice` | 调整某个人物的语气 |

AI 局部重写只要求模型返回目标 `Scene` JSON。服务端会强制保持 `scene.id`、`source_chapter`、`int_ext`、`time_of_day`、`location` 等关键字段不变，并重新通过 `Screenplay.model_validate`。

## YAML Schema

完整设计说明见：

```text
docs/YAML_SCHEMA.md
```

当前顶层结构：

```yaml
schema_version: '1.0'
title: 低智商犯罪
genre: 悬疑 / 犯罪
adaptation_type: 短剧
logline: 围绕《低智商犯罪》核心冲突展开的剧本初稿。
source: {}
global_state: {}
characters: []
scenes: []
```

## 测试

后续 PR 的测试方式统一写成：

```bash
python -m pytest
python -m ruff check .
python -m compileall -q src tests
```
