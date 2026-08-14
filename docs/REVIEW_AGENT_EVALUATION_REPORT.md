# Code Review Agent 评测报告

更新时间：2026-08-14。

## 目标

验证离线 Review Agent 能否在本仓库真实历史中：

1. 检出一个曾导致 CI 失败的缺陷；
2. 在对应修复提交上不产生误报；
3. 输出可重复计算的召回率与误报率。

## 数据集

数据集：`evals/reviewagent/v1/cases.json`（`reviewagent-eval-v1`）。

| Case | Head | 预期 |
| --- | --- | --- |
| `pr66-cross-test-import-defect` | `3b4645c` | `PYTEST_FAILURE` |
| `pr66-cross-test-import-fixed` | `f6d46c6` | clean |

PR #66 在 `tests/test_security.py` 中使用了
`from tests.test_ai_converter import scene_dict`。`tests/` 不是包，pytest 控制台条件下会
触发 `ModuleNotFoundError`；后续提交改为顶层导入
`from test_ai_converter import scene_dict`。

评测定向运行：

```text
pytest -q --disable-warnings --import-mode=append \
  tests/test_security.py::test_converter_prompt_contains_data_fence
```

`append` 是 pytest 的正式 import mode：它稳定复现原 CI 的顶层测试模块导入条件。
测试使用每个 worktree 内独立的 pytest 临时目录，避免本机全局临时目录 ACL 和历史
依赖漂移污染结果。

## 结果

实际运行命令：

```powershell
story2script-review eval `
  --dataset evals/reviewagent/v1/cases.json `
  --repo . `
  --report-prefix pr66-final
```

| Case | 结果 | Finding | 耗时 |
| --- | --- | --- | ---: |
| 缺陷提交 | 命中 | `tests/test_security.py:216` / `ModuleNotFoundError: No module named 'tests'` | 4.043s |
| 修复提交 | 通过 | 无 | 3.571s |

汇总指标：

| 指标 | 数值 |
| --- | ---: |
| 完成 case | 2 / 2 |
| True positive | 1 |
| False negative | 0 |
| False positive | 0 |
| True negative | 1 |
| 召回率 | 100.0% |
| 误报率 | 0.0% |

两个 case 均使用 demo 汇总器，完全离线、无 API Key、无 LLM 调用。临时 worktree 在
运行后已清理。

## 限制

- 当前只有一个缺陷家族和一个干净对照，数字不能外推为通用代码审查准确率。
- 该评测验证工具编排、证据结构和报告统计，不验证 LLM 对业务逻辑缺陷的判断能力。
- pytest、Ruff 和 Bandit 仍受项目依赖与规则版本影响；数据集通过固定测试节点和
  import mode 降低漂移，但没有容器级环境锁定。
