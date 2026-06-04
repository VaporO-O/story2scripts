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

## 测试

```bash
pytest
ruff check .
```

