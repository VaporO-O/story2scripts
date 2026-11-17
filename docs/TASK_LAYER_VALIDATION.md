# 本地任务层验证记录

日期：2026-11-08。功能说明见 [TASK_LAYER_GUIDE.md](TASK_LAYER_GUIDE.md)。运行环境为 Windows、项目 story2script Python 环境；模型响应由测试夹具生成，任务库与供应商配置使用独立临时目录。

## 自动化结果

| 检查 | 结果 |
| --- | --- |
| 全量 pytest（主要实现完成时） | 686 通过，172.39 秒 |
| 最终任务/故障/流式专项回归（含新增中断时间预算用例） | 53 通过，54.23 秒 |
| 原有 dev/holdout 离线回归门禁 | 19 通过 |
| Ruff | 通过 |
| studio.js 语法检查 | 通过 |
| 原有朗读队列 Node 测试 | 2 通过 |

全量测试保留两条现有依赖警告：Starlette TestClient 的 httpx 迁移提示，以及 pydantic-settings 的 lifespan 前向引用提示。

## 故障与并发覆盖

新增 tests/test_job_reliability.py 和 tests/test_studio_jobs.py，调整既有任务测试以验证终态不可回退和异步恢复。

- 不可写数据库、插入事务拒绝：提交明确失败，未启动内存任务。
- 进度或结果写入失败：内存状态不前进，不发布成功事件，不返回未保存的成功结果。
- 成功结果 JSON 损坏：查询明确报存储错误，不继续报告成功。
- 相同 Idempotency-Key 并发提交：12 次请求、6 个并发线程，只得到一个任务和一次执行；相同标识不同参数被拒绝。
- 多个本地 store 同时恢复：活跃执行锁阻止重复运行，尝试次数不因旁观恢复器增加。
- 真实子进程执行中 os._exit：OS 释放锁，新 store 从 SQLite 恢复并成功完成，尝试次数递增。
- 重启/手动继续保留调用和时间预算，最多三次执行。进程中断的未完成调用按预留时间保守计费入预算。
- 用户在失败线程尚未退出时立即恢复：重新派发不会遗漏。
- 数据库首次并发打开时的 WAL 竞争：串行初始化并有限重试；正常读取不反复执行迁移。
- 旧表迁移增加状态版本和去重字段，旧结果仍可读取。
- 工作线程固定使用提交时的数据库路径，运行中环境变量变化不把检查点写到另一数据库。
- 创作任务提交返回 202 时，模型仍可处于执行中；无需等待全部模型响应。
- 重检中途失败：已完成场景响应复用，继续剩余提取与全剧审校，不重复调用已成功步骤。
- 完整结果已写检查点、终态写失败：恢复直接提交保存结果，不重新调用模型。
- 引用不成立的模型响应从检查点移除，允许在剩余预算内重新生成；不把业务校验失败当成可永久复用的成功步骤。
- 没有对白的情绪分析直接返回空报告，不依赖模型配置或调用供应商。
- 本地均匀分组/情绪标注/规则重放仍立即返回，旧 AI 入口明确改为 202 任务响应。
- 冻结原稿、按请求标识查任务、任务历史、排队取消、非法输入、语义检查 valid=false 的任务完成语义均有覆盖。

## 浏览器验收

使用独立本地服务和模拟模型，未调用真实供应商：

1. 提交含三场待重新提取事件的剧本，后台核验执行三个场景步骤及全剧审校，总计 4 次调用。
2. 运行过程中切换面板；刷新页面后“恢复上次创作任务”找回状态、结果和累计预算。
3. 显式载入任务原稿后应用重新核验结果，检查区显示通过。
4. 启动 AI 两幕分组，同时修改并保存工作稿概要；任务完成后自动拒绝覆盖新的工作稿。
5. 再次点击应用旧结果仍被版本检查拦截，修改后的概要保留。
6. 任务历史显示两项已完成任务；控制台无错误。

模型调用日志为三次场景提取、一次全剧审校、一次分组；刷新恢复没有产生额外调用。临时验证服务已停止。

![任务恢复与工作稿保护，模拟模型验证](../evals/reports/task-layer-browser.jpg)

## 结论边界

这些测试验证本地任务状态、持久化、恢复、重复执行防护及结果应用流程，不验证真实模型创作质量。文件锁只用于同主机本地存储；SSE 订阅仍在各服务进程内，未接入跨机器消息队列，仍建议单 Web 进程。

外部模型请求已发出但响应未保存时，进程退出可能导致恢复后重发，无法保证绝对不重复计费。缓存检查点的时间预留是保守预算，不是远端账单时长。

## 复现

```bash
python -m pytest -q
python -m pytest tests/test_job_reliability.py tests/test_studio_jobs.py tests/test_job_store.py tests/test_scene_stream.py -q
python -m ruff check .
node --check src/story2script/static/studio.js
node --test tests/speech_queue.test.cjs
python -m story2script.evaluation run --dataset evals/datasets/v1/dev.json --dataset evals/datasets/v1/holdout.json --baseline evals/baselines/demo-v1.json --output-dir evals/reports --report-prefix task-layer-regression --fail-on-regression
```
