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

## 测试

```bash
pytest
ruff check .
```
