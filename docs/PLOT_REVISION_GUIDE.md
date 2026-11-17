# 剧情改写使用说明

支持死亡/离场、复活/回归、物品转移、地点移动、获知秘密、关系变化，以及自然语言自定义事件。生成使用现有 AI 配置，确定性事件校验不调用模型。

## 工作台操作

1. 生成剧本，或粘贴 YAML 后校验，进入“剧情改写”。
2. 选择执行角色、事件类型、生效场景。转交的执行者是交出者，获知秘密的执行者是学习者。事件发生在该场末尾。
3. 输入要求；根据事件类型填写另一角色、物品、地点、秘密或来源。可留空交给模型识别，预览会展示解析结果。信息来源人物和来源物品二选一，关系选同盟、敌对、中立。
4. 自定义事件直接描述受伤、被捕、康复等要求；模型会生成额外状态与规则，无需手写 JSON。点击“预览修改计划”，核对事件参数、初始事实、假设与影响范围。初始事实不能包含后续才得到的物品或秘密。
5. 原稿账本若有错，在“修正事件账本（高级）”编辑 JSON，点击“校验并保存账本”。保存生成新计划，原计划保留。
6. 原稿账本、本次事件前提通过后，勾选已核对初始事实、假设和自定义规则，生成候选。变更后下游矛盾属于需要改写的内容，不要求推演中所有旧剧情仍成立。
7. 查看正文差异及规则证明。全部检查通过才可采用，采用前再次推演；采用后可回退到改写前。

例：先在第 2 场让林夏死亡，再在该版本的第 5 场选择复活。第 2–4 场仍遵守死亡约束，第 5 场末明确复活后才能继续行动。仅写“似乎还活着”不能自动解除约束。

## 如何检查

每一步展示所需事实、实际状态和来源事件。未知不等于 false，也不会默认为 true。转交要求双方可行动、位置已知且相同、交出者持有物品；信息获取要求同地知情者或本人持有且记载秘密的物品。关系双向更新，物品单一持有。

“原稿规则校验”说明账本是否可执行；“变更后的影响推演”展示哪些旧剧情前提失效。不能为了让检查变绿而捏造前提。若本次事件本身失败，修正参数、账本或生效位置后再规划。

逐字引用不能证明语义理解正确，事件漏提、含糊信息和初始事实时间仍需作者核对。当前是有限状态和事件推演。自定义规则可扩展额外状态、关系和组合步骤，但不支持任意文学因果、旅行时间、概率、数值运算和外部代码。未明确建模的规则不会自动推断。详细规则与边界见 [设计说明](PLOT_REVISION_DESIGN.md)。

## 时间、恢复与版本

普通场景默认 present，历史场景在 YAML 标注：

```yaml
timeline_mode: flashback  # 也支持 recording / dream
event_time_scene_id: scene-1
```

历史使用引用场景开始时的状态，不能引用未来，效果不写回当下。混合时间线需拆场；复活与随后对白分别放入先后两个正文元素。

原稿、计划、候选、正文及事件检查点保存到 SQLite，默认 `.story2script/jobs.db`，沿用 `STORY2SCRIPT_JOBS_DB`。中断后可恢复已完成场景；调用数、时间和尝试次数不会清零。浏览器“恢复上次剧情任务”重连后台，空工作台会恢复原稿，有内容时保留编辑。哈希校验阻止旧结果覆盖新编辑。旧计划须重新预览建立账本，旧版本仍可查看和回退。

默认候选阶段最多 80 次调用、1200 秒，规划另调用一次。每场最多两次生成；未解决矛盾保留为不可采用的候选。沿用单进程服务，不提供分布式任务调度。

原小说章节匹配时冻结快照，作为 source_novel 背景参考；旧稿为 original_script，当前事实及前文为 adapted_revision。改编事实优先，任务独立索引，不复用生成响应缓存。已确认的初始依据保留原版本快照，避免改写后出处消失。

采用后的 AI 局部润色重新提取并校验事件，必须保持该场末状态。需要改状态时使用剧情改写。本地演示若改变正文，提示改用 AI；纯镜头提示可直接重检。手工编辑 YAML 后应重新规划、核对账本。

## HTTP 接口

| 方法与路径 | 用途 |
| --- | --- |
| POST /api/plot/plan | 输入 YAML、指令、执行角色、生效点、事件参数及预算 |
| GET /api/plot/plans/{plan_id} | 恢复计划、原稿及推演 |
| POST /api/plot/logic/check | 输入 yaml_text、logic，直接重放，不调用模型 |
| POST /api/plot/plans/{plan_id}/logic | 输入修正后的 logic，返回新计划 ID 和推演结果 |
| POST /api/plot/jobs | 输入 plan_id、当前 yaml_text、confirm_logic: true |
| GET /api/plot/jobs/{job_id} | 查询进度与候选 |
| POST /api/plot/jobs/{job_id}/resume | 恢复已确认任务及检查点 |
| POST /api/plot/jobs/{job_id}/cancel | 取消尚未执行的任务 |
| GET /api/plot/revisions/{revision_id} | 候选、差异、规则证明及问题 |
| POST /api/plot/revisions/{revision_id}/adopt | 输入当前 yaml_text，重检后返回采用版本 |
| POST /api/plot/revisions/{revision_id}/rollback | 输入当前 yaml_text，校验后返回原版本 |

计划请求示例：

```json
{
  "yaml_text": "...完整剧本...",
  "instruction": "让林夏把仓库钥匙交给周宁，后面由周宁负责开门。",
  "character_id": "character-1",
  "scene_id": "scene-2",
  "operation": "transfer",
  "target": "周宁",
  "item": "仓库钥匙",
  "max_calls": 80,
  "timeout_seconds": 1200
}
```

operation: death / exit / revive / return / transfer / move / learn / relationship / custom。custom 直接使用 instruction 描述，复用已声明规则时可传 custom_rule 和 bindings；详见 [自定义事件说明](CUSTOM_EVENTS.md)。move 使用 destination；learn 使用 secret 及 source_character/source_item；relationship 使用 target 和 ally/enemy/neutral。实体参数接受 ID 或唯一名称，执行角色支持唯一姓名/别名；指令涉及多角色且有歧义时必须选择执行角色。novel_text 可选。

候选 YAML 的 plot_state.logic 包含账本，HTTP 候选结果的 reasoning 包含证明。采用/回退返回文档供工作台应用，不修改用户任意磁盘文件。

## 验证

规则测试见 tests/test_logic_engine.py；模拟模型流程和对抗测试见 tests/test_plot_revision.py。浏览器演示使用模拟模型，不衡量真实模型的抽取准确率、故事吸引力或创作质量。结果见 [逻辑引擎验证报告](LOGIC_ENGINE_VALIDATION.md)。
