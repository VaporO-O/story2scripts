# 参考项目分析与更新路线

分析日期：2026-11-08。对象是当前公开仓库的固定提交快照，采用 README + 实际源码核对；没有安装或运行外部项目，也没有把它们的业务代码复制进本项目。因此这里的“代码可见”不等于运行测试通过。

## 参考范围

- 项目 A：[ZhengDongHang/Novel-to-Script](https://github.com/ZhengDongHang/Novel-to-Script)，提交 `b359441b1c3878a9ce9abbd4bb154df5ce3cfce4`。
- 项目 B：[1dashboard/ai-novel-screenplay](https://github.com/1dashboard/ai-novel-screenplay)，提交 `67e3581f34336fb4c712c63da31fb729004dabf0`。
- 我们：Story2Script 当前工作区，完整已实现清单见 [FEATURES.md](FEATURES.md)。

## 项目 A：值得借鉴的部分

A 的方向是处理流水线：文本抽取 → 情绪分类/动作生成 → 对白改写 → Word → TTS。源码可见情绪模型、动作模型、最近三条文本的上下文输入、断点跳过已处理 ID、Word 段落输出及百度语音合成调用。

| 能力 | 源码观察 | 我们的状态 | 采纳方式 |
| --- | --- | --- | --- |
| 情绪与动作辅助对白 | 转换 prompt 合并 emo_label、behaviour 和前三条原文 | 有人物语气、parenthetical、潜台词和戏剧化决策，没有独立情绪轨迹 | 增加“情绪变化/行动意图”审校与可选提示，先用现有模型；先评估收益再考虑专用模型 |
| Word 剧本 | CSV 行写为角色对白或旁白段落，设置中文字体 | 目前主要导出 YAML | 从我们已有 Screenplay 渲染 Word，并保留场景标题、角色、对白/动作与版本信息 |
| TTS 朗读 | 逐段合成 MP3 后播放，固定声线参数 | 尚无音频 | 可做后续角色分声线试听；保留台词顺序和说话人 ID，按台词缓存 |
| 数据集/对照思路 | 仓库含动作/情绪相关数据、不同转换脚本 | 已有离线评测、对白/RAG 回归集 | 增加“情绪提示/动作提示有无”的对照，考察信息保留和人类偏好 |
| 前文历史 | 使用紧邻前三行背景 | 已有词法/语义混合检索、章节截止、版本事实 | 保留我们的检索与状态推演，补充局部对话窗口即可，无需替换成固定三句 |

不建议把训练 BERT/Seq2Seq、爬取小说、固定声线逐段播放列为当前主线。用户最直接的需求是可靠改编及可用成稿；这些路线需要明确数据、场景与收益验证。A 的 README 关于效果的评价不作为我们自己的性能证据。

源码依据：

- [情绪、动作及局部历史输入](https://github.com/ZhengDongHang/Novel-to-Script/blob/b359441b1c3878a9ce9abbd4bb154df5ce3cfce4/code/B_%E5%B0%8F%E8%AF%B4%E8%A7%A3%E6%9E%90/%E8%BE%85%E5%8A%A9%E6%A8%A1%E5%9D%97-%E6%96%87%E6%9C%AC%E8%BD%AC%E5%AF%B9%E8%AF%9D/convert.py#L42)
- [情绪推理脚本](https://github.com/ZhengDongHang/Novel-to-Script/blob/b359441b1c3878a9ce9abbd4bb154df5ce3cfce4/code/B_%E5%B0%8F%E8%AF%B4%E8%A7%A3%E6%9E%90/%E8%BE%85%E5%8A%A9%E6%A8%A1%E5%9D%97-%E6%83%85%E7%BB%AA%E5%88%86%E6%9E%90/eval.py)
- [动作模型推理](https://github.com/ZhengDongHang/Novel-to-Script/blob/b359441b1c3878a9ce9abbd4bb154df5ce3cfce4/code/B_%E5%B0%8F%E8%AF%B4%E8%A7%A3%E6%9E%90/%E8%BE%85%E5%8A%A9%E6%A8%A1%E5%9D%97-%E5%8A%A8%E4%BD%9C%E5%88%86%E6%9E%90/predict.py)
- [Word 输出](https://github.com/ZhengDongHang/Novel-to-Script/blob/b359441b1c3878a9ce9abbd4bb154df5ce3cfce4/code/C_%E5%89%A7%E6%9C%AC%E7%94%9F%E6%88%90/%E5%89%A7%E6%9C%AC%E7%94%9F%E6%88%90.py#L11)
- [TTS 合成与播放](https://github.com/ZhengDongHang/Novel-to-Script/blob/b359441b1c3878a9ce9abbd4bb154df5ce3cfce4/code/D_%E6%9C%89%E5%A3%B0%E5%89%A7%E6%9C%AC%E8%BD%AC%E6%8D%A2/TTS.py#L17)

## 项目 B：产品体验更值得借鉴，但需区别说明与代码

| 能力 | 固定提交中的证据 | 结论 |
| --- | --- | --- |
| 多格式输入 | DOCX、PDF、旧 DOC 解析器存在 | 可以借鉴；PDF 代码使用文本抽取，未见 OCR，不把扫描 PDF 算已支持 |
| 角色关系图 | CharacterGraph.tsx 使用 SVG、关系数据、选中角色回调和导出逻辑 | 可借鉴图形交互；我们应使用稳定 ID，并增加场景时间与版本切换 |
| 多格式输出 | export.ts 有 TXT、HTML、DOCX；HTML 打开打印对话框 | 可借鉴；“PDF”路径是浏览器打印，并非后台 PDF 排版服务 |
| 幕结构 | converter.py 的 _group_into_acts 为固定比例三幕，小于 6 场归一幕 | 可借鉴分层导航；该快照没有落实 README 所述模型智能 2–7 幕划分 |
| 章节并行 | convert 的逐章 for 循环依次调用 _analyze_chapter | 不引用 README 的“6 线程/4 倍提速”为当前代码或我们的性能事实 |
| Prompt 模板 | FileUploader.tsx 有选模板与自定义 prompt 的界面和拼接逻辑，但 imports 的 utils/templates.ts 不在 Git 树 | 只能确认入口/调用代码，模板库未完整公开；我们自行设计带版本的模板 |
| 内联编辑、AI 编剧助手 | 详情页引用 SplitScreenplayPreview、ChatSidebar，相关文件未在 Git 树；后端引用缺失 chat_service | 不能确认公开快照可直接完整运行这些功能；产品思路可借鉴 |
| 分布式进度 | stream.py 是进程内 asyncio.Queue 注册表 | 与 README 的 Redis Pub/Sub 桥接描述不一致；不可当成已验证分布式实现 |
| 云上传/任务运行 | 路由引用未公开 cos_service；Git 树未见 README 的 celery_app.py/tasks.py | 本轮未运行，不能给部署可用性结论 |

源码依据：

- [顺序逐章转换](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/src/converter.py#L84)
- [固定三幕分组](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/src/converter.py#L469)
- [人物图与场景联动回调](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/frontend/src/components/conversion/CharacterGraph.tsx#L45)
- [文本、HTML 与 DOCX 导出](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/frontend/src/utils/export.ts#L7)
- [模板入口及未公开的模板 import](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/frontend/src/components/conversion/FileUploader.tsx#L1)
- [详情页及组件依赖](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/frontend/src/pages/ConversionDetailPage.tsx#L1)
- [服务依赖与聊天路由](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/backend/api/conversion.py#L32)
- [进程内 SSE 队列](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/backend/services/stream.py#L1)
- [PDF 文本抽取](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/src/parsers/pdf_parser.py#L25)
- [DOCX 解析器](https://github.com/1dashboard/ai-novel-screenplay/blob/67e3581f34336fb4c712c63da31fb729004dabf0/src/parsers/docx_parser.py)

上述缺失是针对指定提交的 Git 树，不代表作者其他分支或未公开版本没有实现。只凭 README 不足以进行工程选型。

## 我们应保留并继续加强的能力

我们已有的优势方向是结构化改编而非单纯生成文本：原文对白归属证据、带时间截止的 RAG、可追溯事件账本、三值前提判断、失败原子性、跨场景依赖、候选差异/回退、冻结定义、采用前重检，以及可复现的模型/检索评测。

这些功能在已检查的两个快照中，没有看到与我们同等的完整确定性剧情规则链路；不能因此断言对方项目在所有方面弱于我们。两者的情绪/动作处理、文档输出和交互呈现，恰好能补我们当前的使用体验。

## 建议采纳顺序

下面保留最初的优先级排序。后续用户已授权五项更新，P1-1/P1-2/P1-3/P2-1/P2-3/P2-4 及设备分角色试听已落地，具体边界见 [创作面板](AUTHORING_STUDIO.md)。DOCX/PDF 导入、云 TTS 与多用户部署仍是计划。

| 优先级 | 更新项 | 具体交付 | 依赖与验收 |
| --- | --- | --- | --- |
| P0，本轮已实现 | 自然语言自定义事件 | 额外枚举状态、参数化规则、组合内置步骤、行动/物品限制、规则预览与冻结 | 规则前提不成立时不能采用；后一步失败回滚整个事件；受伤后行动被阻止；见 CUSTOM_EVENTS.md |
| P1-1 已实现 | 剧情时间线 + 人物关系图 + 证据跳转 | 点击角色/关系可定位涉及场景；切换场景查看当时状态；查看影响路径 | 复用事件来源和稳定 ID，不能把最终状态误画成早期状态；原文/候选区别展示 |
| P1-2 已实现 | 受控改编模板 | 悬疑、喜剧、短剧、舞台等“改编偏好”，自定义风格/保真程度/节奏，保存模板版本 | 作为创作数据进入现有受控输出协议；不能让自由 prompt 覆盖 Schema 或事实约束；做保真与偏好对照 |
| P1-3 已实现 | TXT / HTML / DOCX 导出与打印 | 使用当前已采用版本生成可读剧本；可选封面、场景标题、角色/道具附表 | 特殊字符、中文字体、分页与对白样式正确；候选须有明确标识；避免导出过期原稿 |
| P2-1 已实现组织层 | 分幕/分集与场景树 | 大纲 → 幕/集 → 场景导航，时长/节奏目标和人工调整 | 第一阶段只增加组织层，保持场景 ID；增删/重排要重新校验事件时间、证据和依赖 |
| P2-2 | DOCX / 文本型 PDF 导入 | 统一解析器接口、页/段落来源、失败提示 | PDF 无文本时明确提示；OCR 单独规划；旧 DOC 低优先级，避免静默乱码 |
| P2-3 已实现 | 内联编辑 + 版本自动保存 | 编辑对白/动作，无需直接改 YAML；保存草稿并展示待检查状态 | 任何正文修改使旧事件提取失效，重新提取/检查后才能称为已验证；哈希冲突不能自动覆盖 |
| P2-4 已实现轨迹与解释 | 情绪与角色声音 | 显式情绪变化、说话意图与行动提示，图形化跨场景查看 | 先用已有模型与 parenthetical/subtext，独立测是否改善对白并保留原意 |
| P3 已实现设备试听 | 分角色朗读 | 每个角色/旁白选择设备声线、语速、音高、音量；完整剧本或单场播放 | 当前使用浏览器语音，不生成音频文件；云 TTS、音频缓存和下载仍是后续扩展 |
| 按部署需求 | 项目库/多用户/分布式队列 | 多作品长期管理、权限、配额、worker 调度 | 有多用户部署目标后再引入，已按用户授权接入可选 Redis/Celery 同主机模式；多用户、跨机器数据库和云存储仍待需求明确 |

已完成的对应实现使用现有账本与稳定 ID：关系按时点重放，正文编辑使旧提取失效，草稿版本保护覆盖冲突。真实模型的模板偏好、分幕和情绪质量对照仍需后续评测。

## 文档维护方式

FEATURES.md 是当前功能总账；PLOT_REVISION_GUIDE.md 是使用流程；CUSTOM_EVENTS.md 解释自定义事件；LOGIC_ENGINE_VALIDATION.md 记录批次测试。每次将路线项改为“已实现”，同时添加入口、边界与测试结果，不能仅凭 README 文案或截图宣布完成。
