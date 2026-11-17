# Redis 集成验证记录

日期：2026-11-08。当前实现与启动方式见 [REDIS_GUIDE.md](REDIS_GUIDE.md)。没有使用真实模型服务或用户小说进行本轮验证。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 全量 pytest，启用真实 Redis 集成测试 | 707 通过，204.40 秒 |
| 其中 Redis 单元/模拟故障测试 | 16 项 |
| 最终补充回归（含 TLS 证书验证） | 17 通过，4.49 秒 |
| 其中真实 Redis + 独立 Celery 进程测试 | 4 项；单独执行时 24.23 秒 |
| 原有 dev/holdout 回归门禁 | 19 通过 |
| 朗读队列 Node 测试 | 2 通过 |
| Ruff、前端语法、Docker Compose 配置检查 | 通过 |

依赖警告与此前一致：Starlette TestClient 的 httpx 迁移提示、pydantic-settings lifespan 前向引用提示，不影响本轮测试结论。

## 测试覆盖

`tests/test_redis_backend.py` 使用 fakeredis/Lua 和模拟 Broker 验证：Web 只投递任务 ID；Redis 不可用时任务与 outbox 仍持久化；补投与重复消息去重；恢复代次隔离；正在执行的任务不会被重投副本重复运行；缓存跨实例共享、TTL 和命名空间隔离、禁用/体积限制、缓存故障按 miss 处理；原子全局限额、账号隔离、生成与 Embedding 独立配额、窗口重置、限流故障拒绝发远端请求；缓存命中不消耗配额，主动重新生成不吃缓存；Prompt 版本与供应商账号隔离；事件有序、限长、有 TTL；事件发送故障不会撤销已提交结果；基础设施状态不泄露凭证；任务拒绝不匹配的数据库部署标识。

`tests/test_redis_integration.py` 连接实际 Redis 服务，另起 Python Celery Worker 进程：

1. Web 侧提交本地演示转换，观察 queued；启动 Worker 后生成完成，SQLite 结果可读取，Redis Stream 含场景与 done。
2. 故意不发布数据库任务，Worker 补投循环自动修复并执行。
3. 一个进程写缓存并占用请求额度，另一个进程命中缓存且被同一额度限制，验证真正跨进程共享。
4. 重检 Worker 在第二个场景调用时 os._exit；新进程恢复。第一个场景调用日志只有一次，失败场景调用两次，最终审校通过，尝试次数 2、累计调用 4。

每个集成测试使用随机命名空间和独立 SQLite 文件，只清理自己的 Redis key；没有 FLUSHDB/FLUSHALL。CI 新增独立 redis-integration job，使用 Redis 7.4 服务运行真实进程测试。

## 本机运行验收

Windows Docker Desktop 上拉取并启动官方 `redis:7.4-alpine`。固定测试端口被 Windows 保留后，改为 Docker 分配的本机端口进行验证；没有改动系统端口策略。

Web 和 Worker 为两个独立后台 Python 进程，使用相同的隔离 SQLite、Redis 命名空间和测试配置。浏览器提交三章原创小故事，模型模式为本地演示，任务执行后生成三场剧本；SSE/状态查询完成。

“运行指标”显示任务后端 redis、缓存 redis、Redis 已连接、在线 Worker 1、等待确认投递 0、生成/向量 RPM 60/120。界面明确标明下方指标仅属于 Web 进程，不冒充 Worker 聚合统计。控制台没有错误。

![真实 Redis 与独立 Worker 的浏览器验收](../evals/reports/redis-worker-browser.jpg)

临时 Web、Worker 和测试 Redis 容器在验收后清理；供应商 `.env` 和默认本地运行方式未被覆盖。正式使用按 REDIS_GUIDE.md 启动 Compose、Worker、Web。

## 结论边界

验证的是同主机 Web/Worker 分离、共享缓存/限额、消息补投、进度传递与检查点恢复。不是跨机器数据库共享、超大并发压测或真实 LLM 创作质量评测。Redis 固定窗口限流控制请求次数，不是 Token 或费用限额。

SQLite 仍是正式数据源，OS 文件锁仍参与执行互斥。跨机器部署需继续迁移关系数据库和分布式执行租约。远端请求已成功但本地未保存时仍可能重新计费，不能承诺 exactly-once。

## 复现

```powershell
python -m pip install -e ".[dev,redis]"
docker compose -f compose.redis.yml up -d
$env:STORY2SCRIPT_TEST_REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:STORY2SCRIPT_DISABLE_DOTENV = '1'
python -m pytest -q
python -m pytest tests/test_redis_integration.py -q
```

没有配置 STORY2SCRIPT_TEST_REDIS_URL 时，仅跳过四项真实 Redis 测试；本地测试和已安装 Redis 依赖的模拟测试可运行。端口不同需要同步修改测试 URL。
