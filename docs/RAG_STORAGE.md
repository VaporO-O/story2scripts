# 持久化故事检索

故事检索默认使用 SQLite 与 `sqlite-vec` 保存小说分块和 Embedding，重启服务后仍可复用。词法检索仍可离线使用；Embedding 或向量库不可用时，普通问答/转换检索会回退到词法结果并记录降级状态。

## 建立和管理索引

为当前导入的小说配置好 Embedding 服务后，在工作台的“故事问答”区域建立索引。AI Embedding 也会在语义/混合检索首次遇到新原文时自动建立或增量更新索引；相同内容和模型配置会复用已有索引。索引接口如下：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/rag/indexes` | 查看当前命名空间的索引 |
| `POST` | `/api/rag/indexes` | 为请求中的 `novel_text` 建立或复用索引 |
| `DELETE` | `/api/rag/indexes/{index_id}` | 删除当前命名空间下的索引 |

建立索引的请求示例：

```json
{"novel_text":"第一章\n林夏在车站找到一封信。"}
```

`POST` 需要可用的 `AI_EMBED_MODEL` 和兼容 Embedding API；未配置模型或调用失败时会返回错误，不会把临时词法索引报告成持久化成功。`DELETE` 会删除该索引保存的分块、元数据，以及没有被其他索引共用的向量；不会修改小说输入、剧本或任务记录。

## 配置

在服务进程环境中设置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_STORAGE` | `sqlite` | `sqlite` 启用持久化；`memory` 关闭磁盘向量存储 |
| `STORY2SCRIPT_RAG_DB` | 任务数据库旁的 `story_vectors.db` | 向量数据库文件完整路径 |
| `RAG_NAMESPACE` | `story2script` | 索引隔离名称，只允许字母、数字、`_` 和 `-` |
| `RAG_EMBEDDING_REVISION` | `1` | 手动隔离同名模型的向量版本；模型维度/行为变更时递增 |
| `RAG_CHUNK_CHARS` | `600` | 原文分块长度；改变后会生成独立索引 |
| `AI_EMBED_MODEL` | 未设置 | Embedding 模型名称；还需配置 `AI_API_KEY` 和 `AI_BASE_URL` |

SQLite 文件默认路径为当前任务数据库所在目录下的 `story_vectors.db`。如果设置了 `STORY2SCRIPT_JOBS_DB`，向量库会与该任务库放在同一目录；设置 `STORY2SCRIPT_RAG_DB` 可以让向量库使用单独路径。多进程部署的 Web 和 Worker 必须看到同一个 SQLite 文件；不同机器之间不适合用本地 SQLite 文件共享。

`RAG_NAMESPACE` 用于在同一数据库中划分索引集合，不是登录或访问控制机制。共享同一 SQLite 文件和命名空间的进程可以访问对应索引。索引身份还会隔离 Embedding 服务地址、模型名、账号指纹、版本、分块清单和正文哈希；凭证原文不会出现在索引列表中。

## 存储与限制

- 数据库保存小说分块原文、章节/文档元数据和浮点 Embedding。请按原文敏感级别设置文件权限并纳入适当备份策略；删除索引不会清除外部备份。
- 持久化语义检索使用 `sqlite-vec` 的精确余弦检索，章节范围会先过滤候选片段，再做语义排名。当前没有近似最近邻结构或大型多租户语料的性能承诺。
- 同一模型下相同正文片段会共享向量，改动小说后仅为新片段请求 Embedding；内容不变时可以复用完整索引。
- `RAG_STORAGE=memory` 不会创建或读取持久化索引。词法模式仍可工作；Embedding 请求在该模式下只适用于当前请求周期。
- SQLite 和扩展不可用时，管理接口返回错误，普通 RAG 检索则可降级到词法结果。配置持久化模式时不会静默报告索引已保存。

安装项目依赖即可安装 `sqlite-vec`：

```bash
pip install -e ".[dev]"
```

相关实现位于 `src/story2script/vector_store.py`、`src/story2script/rag.py` 和 `src/story2script/rag_api.py`；向量库回归测试位于 `tests/test_vector_store.py`。
