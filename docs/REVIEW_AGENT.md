# Code Review Agent

`reviewagent` 是与 `story2script` 平级的新包，用 LangGraph 编排代码审查工具，
不改造现有小说改编 Agent。它支持完全离线的确定性模式，也可以在汇总阶段复用
Story2Script 已有的 `LLMClient`。

## 工作流

```text
START
  -> plan
  -> Send(diff, ruff, pytest, bandit)
  -> synthesize
  -> interrupt(review_approval)
  -> Command(resume={approved, comment})
  -> END
```

- `diff`：读取 `base...head` 的提交差异，供 AI 汇总器分析。
- `ruff`：只检查本次变更中的 Python 文件。
- `pytest`：默认运行当前 checkout 的完整测试集，可安全限定测试路径或节点。
- `bandit`：只检查本次变更中的 Python 文件。
- `synthesize`：demo 模式合并工具 finding；AI 模式额外分析 diff，但不会丢弃工具 finding。
- `interrupt`：报告必须经过显式批准或拒绝，审批结果才能成为最终状态。

四个工具通过 LangGraph `Send` 在独立线程中扇出。所有命令都使用固定参数数组和
`shell=False`，Git revision 拒绝空白和 `-` 前缀。写入 SQLite 前，输出会先脱敏，
再按有界长度截断。

## 运行

推荐使用独立环境：

```powershell
conda activate story2script
python -m pip install -e ".[review]"
```

Review Agent 的 LangGraph、Bandit、Ruff 和 pytest 位于 `review` 可选依赖组；普通
Story2Script 运行时不会被迫安装它们。`dev` 组也包含同一组工具，因此项目开发环境和
CI 继续使用 `pip install -e ".[dev]"` 即可。

完全离线运行：

```powershell
story2script-review run `
  --repo . `
  --base origin/main `
  --head HEAD `
  --mode demo
```

命令完成工具阶段后会打印 thread id，并停在 `awaiting_approval`。在另一个进程中恢复：

```powershell
story2script-review resume `
  --checkpoint .reviewagent/review.sqlite3 `
  --thread-id <thread-id> `
  --approve `
  --comment "已核对"
```

拒绝时把 `--approve` 换成 `--reject`。JSON 和 Markdown 报告默认写到
`.reviewagent/reports/`，SQLite checkpoint 默认是 `.reviewagent/review.sqlite3`；
整个 `.reviewagent/` 已被 Git 忽略。

AI 汇总模式：

```powershell
story2script-review run --repo . --base origin/main --head HEAD --mode ai
```

AI 模式读取现有 `.env` 中的 `AI_*` 配置，并通过 `LLMClient.complete_json` 发起请求，
因此沿用现有的指标、Token 统计、缓存协议和错误脱敏。Review Agent 不引入
`langchain` 或 `langchain-openai` 主包。

## SQLite 与恢复

本地 MVP 使用 `SqliteSaver`。SQLite 文件能持久化每个 super-step 和 interrupt，
因此首次运行结束后可以关闭进程，再由 `resume` 恢复同一个 thread。纯内存 saver
无法支持这种跨进程恢复。

MySQL 不是必需项，也不能直接传给 `SqliteSaver`。如果未来变成多机服务，再引入
对应的 checkpoint saver、连接管理和迁移方案；本地单用户 CLI 使用 MySQL 只会增加
部署和运维成本。

## 定向 pytest

常规审查默认跑完整测试集。大型仓库可缩小范围：

```powershell
story2script-review run `
  --repo . `
  --base origin/main `
  --tool pytest `
  --pytest-target tests/test_security.py `
  --pytest-target tests/test_llm_client.py
```

目标必须是仓库内相对路径，可使用 `path::test_name` 节点形式。pytest 的临时目录位于
仓库 `.reviewagent/` 下的唯一目录中，执行后自动清理，避免依赖机器的全局临时目录。

## 历史评测

```powershell
story2script-review eval `
  --dataset evals/reviewagent/v1/cases.json `
  --repo . `
  --report-prefix reviewagent-v1 `
  --min-recall 1.0 `
  --max-false-positive-rate 0.0
```

评测器会为每个历史提交创建临时 detached worktree，运行后通过
`git worktree remove --force` 清理。它不会在当前 checkout 上运行历史 head 的 Ruff、
pytest 或 Bandit，因为那会把历史 diff 与当前文件混在一起。普通 `run` 也默认要求
`head` 等于当前 checkout；显式允许历史 head 时只可运行 `diff`。

当前评测集包含 PR #66 的真实失败提交和对应修复提交，详细结果见
[`REVIEW_AGENT_EVALUATION_REPORT.md`](REVIEW_AGENT_EVALUATION_REPORT.md)。

## 边界

- Git 范围是已提交的 `base...head`，不会包含未提交工作区修改。
- demo 模式只汇总工具提供的客观 finding，不尝试凭规则猜测业务逻辑缺陷。
- AI 模式可以分析 diff，但 AI 评审不等于人工评审，仍必须经过 interrupt 审批。
- SQLite checkpoint 和报告可能包含代码片段，应只保存在受控环境。
