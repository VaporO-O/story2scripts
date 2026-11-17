# 五项创作能力验证记录

日期：2026-11-08。实现与使用方式见 [AUTHORING_STUDIO.md](AUTHORING_STUDIO.md)，全项目功能清单见 [FEATURES.md](FEATURES.md)。本轮没有调用真实 LLM 服务。

## 自动化结果

| 检查 | 结果 |
| --- | --- |
| 最终全量 pytest | 657 通过，107.89 秒 |
| 新增创作接口与模型用例 | 32 项，包含在全量结果中 |
| 最终补充回归（源码状态保护 + 创作接口 + Schema） | 44 通过，7.93 秒 |
| 浏览器朗读队列 Node 测试 | 2 通过 |
| 原有 dev/holdout 离线回归门禁 | 19 通过 |
| Ruff | 通过 |
| app.js / studio.js / speech_queue.js 语法检查 | 通过 |
| Schema 新字段及引用一致性 | 通过 |

保留两条既有依赖警告：Starlette TestClient 的 httpx 迁移提醒，以及 pydantic-settings lifespan 前向引用提醒。

测试验证：幕/集完整连续覆盖、非法重排和重复拒绝、AI 分组输出约束、正文编辑使旧证明失效、源代码校验识别变化、选择场景边界防止未来关系泄漏、初始/事件来源定位、同场关系区别于明确关系、草稿版本冲突与不可变历史、无法通过普通保存伪造已检查、模板不可变版本、转换偏好快照、情绪逐字证据和说话人匹配、导出最新文本与 HTML 转义、Word 文本和表格、规则重检不擅自清除语义待检查状态、AI 重检的冻结前提与失败标记、旧哈希兼容。

朗读队列用例检查长对白分段的 Unicode 字符、换行和标点完整性，角色/场景顺序及跳过旁白的行为，已接入 CI。

## 浏览器验收

使用独立 localhost 服务和隔离 SQLite，测试剧本来自模拟模型，不使用用户的真实小说和供应商配置。

- 载入含关系变化和对白的四场剧本，结束时关系显示敌对，切换到该场开始时不提前显示敌对。
- 点击角色查看存活、位置和关系，点击关系追溯事件，继续定位到原文段落。
- 预览两幕分组，修改幕标题和目标 12 分钟，应用后场景树正确展示；场景顺序未变。
- 两次修改动作并保存，第二次可自动保存；修改对白后立即切换面板，尚未完成的编辑先保存。
- 旧事件正文引用失效后显示重新核验提示；不显示“仍然已通过”。
- 刷新页面，从数据库恢复草稿第 3 版；继续保存至第 5 版，历史目录能载入第 1 版，原历史不覆盖。
- 选择悬疑模板，修改补充要求并保存自定义模板 v1，应用到工作稿。
- 浏览器成功下载 Word；程序打开下载结果，确认含最新编辑、幕标题和待核验草稿标签。
- 情绪轨迹区分“疑惑”与“未标注”，有原句证据；角色声线控件枚举到 6 种设备声线，包含中文本地声线。
- 音量设为 0，完整剧本 8 个朗读片段按旁白/角色顺序播放，测试暂停/继续，最终显示“朗读完成”；未进行主观音质评价。
- 浏览器控制台无错误。临时验证服务已停止。

![剧情关系和状态时点验证](../evals/reports/studio-graph.jpg)

![分幕导航与内联编辑验证](../evals/reports/studio-editor.jpg)

截图为本地模拟模型流程验证，不能用来衡量真实模型的改编效果。

## Word 版式验收

通过应用导出器生成包含中文标题、两幕、四场、对白、表演指示、人物表和道具表的测试 DOCX。使用 bundled Python 和 documents 技能的 render_docx.py，通过 LibreOffice 渲染为两页，逐页检查。

检查项：中文正常显示、标题没有模板残留的蓝色横线、对白有缩进、说话人与下一段保持连接、表头重复标记、附表边框、页码、无裁切和重叠。输出采用 Letter 纵向页面，正文保持可编辑。

内部 QA 位于 `.pytest-tmp/studio-document-qa/`，其中渲染图片和 PDF 用于版式检查，不作为产品的直接 PDF 导出功能。应用的 PDF 路径为导出 HTML 后浏览器打印。

## 实际边界

AI 分幕、风格改写、情绪解释及语义重检的协议已有模拟模型测试，未做真实模型文学质量或效果提升对照。关系图的真实性依赖已确认事件账本及初始假设；代码不能证明抽取没有遗漏。

朗读依赖浏览器/设备语音，不生成可下载音频，不承诺跨平台声线一致。默认本地转换仅保存创作偏好，不会模拟理解模板文字。目标分钟数是作者输入，不是自动片长预测。

## 复现

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
node --test tests/speech_queue.test.cjs
node --check src/story2script/static/studio.js
python -m story2script.evaluation run --dataset evals/datasets/v1/dev.json --dataset evals/datasets/v1/holdout.json --baseline evals/baselines/demo-v1.json --output-dir evals/reports --report-prefix studio-regression --fail-on-regression
```
