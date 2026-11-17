/* Plot revisions are candidates until explicitly adopted. Render model text as text. */
(() => {
  const el = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "plotCharacter", "plotOperation", "plotScene", "plotInstruction", "plotMaxCalls", "plotTimeout",
    "plotPlanButton", "plotRestoreButton", "plotStatus", "plotPlanPreview", "plotGenerateButton",
    "plotResumeButton", "plotRevisionPreview", "plotAdoptButton", "plotRollbackButton", "plotDownloadButton",
    "plotTarget", "plotItem", "plotDestination", "plotSecret", "plotSourceCharacter", "plotSourceItem", "plotRelationship",
  ].map((id) => [id, el(id)]));
  const storageKey = "story2script.plot.lastJob";
  let plan = null;
  let revision = null;
  let jobId = "";
  let baseYaml = "";
  let busy = false;
  let pollGeneration = 0;
  let plannedForm = "";
  let logicConfirmed = false;
  let logicDirty = false;
  let propertyNames = new Map();
  let ruleNames = new Map();
  const labels = { death: "死亡", exit: "离场", revive: "复活", return: "回归", transfer: "物品转移", move: "地点移动", learn: "获知秘密", relationship: "关系变化", act: "行动", use_item: "使用物品", custom: "自定义事件" };
  const fields = { target: "plotTarget", item: "plotItem", destination: "plotDestination", secret: "plotSecret", source_character: "plotSourceCharacter", source_item: "plotSourceItem", relationship: "plotRelationship" };
  const fieldLabels = { target: "另一角色", item: "物品", destination: "目的地", secret: "秘密", source_character: "来源人物", source_item: "来源物品", relationship: "关系" };

  function eventParameters() {
    return Object.fromEntries(Object.entries(fields).filter(([, id]) =>
      ui[id].parentElement.dataset.operations.split(" ").includes(ui.plotOperation.value)
    ).map(([key, id]) => [key, ui[id].value.trim()]));
  }

  function eventFields() {
    el("plotCustomHint").classList.toggle("hidden", ui.plotOperation.value !== "custom");
    el("plotEventFields").querySelectorAll("[data-operations]").forEach((label) =>
      label.classList.toggle("hidden", !label.dataset.operations.split(" ").includes(ui.plotOperation.value)));
  }

  function ready() {
    const target = plan?.intervention_reasoning?.steps.find((s) => s.event_id === `intervention:${plan.change.change_id}`);
    return !!plan?.baseline_reasoning?.valid && target?.status === "applied" && plan.estimated_calls <= plan.max_calls &&
      !plan.intervention_reasoning.issues.some((i) => i.code === "goal" && i.scene_id === plan.change.effective_scene_id);
  }

  function formSignature() {
    return JSON.stringify([ui.plotInstruction.value.trim(), ui.plotCharacter.value,
      ui.plotScene.value, ui.plotOperation.value, ui.plotMaxCalls.value, ui.plotTimeout.value, eventParameters()]);
  }

  function status(text, error = false) {
    ui.plotStatus.textContent = text;
    ui.plotStatus.classList.toggle("plot-error", error);
  }

  function buttons() {
    ui.plotPlanButton.disabled = busy;
    ui.plotRestoreButton.disabled = busy;
    ui.plotGenerateButton.disabled = busy || !plan || !!plan.unresolved_questions.length || !logicConfirmed || logicDirty || !ready();
    ui.plotAdoptButton.disabled = busy;
    ui.plotRollbackButton.disabled = busy;
    ui.plotResumeButton.disabled = busy;
  }

  async function api(path, body) {
    const response = await fetch(`/api/plot/${path}`, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(errorMessage(data, "剧情改写失败"));
    return data;
  }

  function refreshChoices() {
    const selected = ui.plotCharacter.value;
    const selectedScene = ui.plotScene.value;
    ui.plotCharacter.replaceChildren(new Option("从指令识别", ""));
    (latestScreenplay?.characters || []).forEach((c) => ui.plotCharacter.add(new Option(c.name, c.id)));
    if ([...ui.plotCharacter.options].some((o) => o.value === selected)) ui.plotCharacter.value = selected;
    ui.plotScene.replaceChildren();
    (latestScreenplay?.scenes || []).forEach((s, i) =>
      ui.plotScene.add(new Option(`第 ${i + 1} 场 · ${s.location}`, s.id)));
    ui.plotScene.value = [...ui.plotScene.options].some((o) => o.value === selectedScene)
      ? selectedScene : currentSceneId;
  }

  function showTab(plot) {
    el("localRewritePanel").classList.toggle("hidden", plot);
    el("plotRewritePanel").classList.toggle("hidden", !plot);
    el("localRewriteTab").classList.toggle("is-active", !plot);
    el("plotRewriteTab").classList.toggle("is-active", plot);
    if (plot) refreshChoices();
  }
  el("plotRewriteTab").addEventListener("click", () => showTab(true));
  el("localRewriteTab").addEventListener("click", () => showTab(false));

  function renderPlan() {
    ui.plotPlanPreview.replaceChildren();
    if (!plan) return;
    propertyNames = new Map((plan.logic?.custom_properties || []).map((p) => [p.id, p.name]));
    ruleNames = new Map((plan.logic?.custom_rules || []).map((r) => [`custom:${r.id}`, r.name]));
    const names = characterNameMap(latestScreenplay || {});
    const target = names.get(plan.change.character_id) || plan.change.character_id;
    ui.plotPlanPreview.append(createElement("h3", "", `${target} · ${labels[plan.change.operation]}`));
    const entities = new Map((plan.logic?.entities || []).map((e) => [e.id, e.name]));
    const params = Object.keys(fields).filter((k) => plan.change[k]).map((k) => `${fieldLabels[k]}：${entities.get(plan.change[k]) || ({ ally: "同盟", enemy: "敌对", neutral: "中立" }[plan.change[k]]) || plan.change[k]}`);
    if (params.length) ui.plotPlanPreview.append(createElement("p", "", params.join("；")));
    if (plan.change.operation === "custom") renderCustomEvent(entities);
    for (const requirement of plan.change.requires || []) ui.plotPlanPreview.append(createElement("p", "", `本次事件还要求：${factText(requirement, entities)}`));
    ui.plotPlanPreview.append(createElement("p", "", `修改要求：${plan.change.instruction}`));
    ui.plotPlanPreview.append(createElement("p", "", `生效点：${plan.change.effective_scene_id} 场末；检查后续 ${plan.scenes.length} 场。预计 ${plan.estimated_calls} 次调用，最多 ${plan.max_calls} 次（含修复）。`));
    for (const question of plan.unresolved_questions) {
      ui.plotPlanPreview.append(createElement("p", "plot-error", `待确认：${question}`));
    }
    for (const warning of plan.warnings) {
      ui.plotPlanPreview.append(createElement("p", "scene-chat-hint", warning));
    }
    renderLogicPlan(entities);
    for (const scene of plan.scenes) {
      const details = document.createElement("details");
      details.className = "plot-scene";
      const kind = { direct: "直接影响", indirect: "间接影响", scan: "一致性扫描" }[scene.impact];
      details.append(createElement("summary", "", `${scene.scene_id} · ${kind}`));
      details.append(createElement("p", "", scene.reason));
      const beats = document.createElement("ul");
      for (const beat of scene.replacement_beats) beats.append(createElement("li", "", beat));
      details.append(beats);
      const dependency = plan.dependencies.find((d) => d.scene_id === scene.scene_id);
      if (dependency?.depends_on_scene_ids.length) {
        details.append(createElement("p", "scene-chat-hint", `依赖：${dependency.depends_on_scene_ids.join("、")}`));
      }
      ui.plotPlanPreview.append(details);
    }
    ui.plotGenerateButton.classList.toggle("hidden", !!plan.unresolved_questions.length);
  }

  function valueText(value, entities) {
    return value == null ? "未知" : entities.get(value) || ({ true: "是", false: "否", ally: "同盟", enemy: "敌对", neutral: "中立" }[value]) || value;
  }

  function factText(f, entities) {
    const name = (v) => valueText(v, entities);
    const predicate = propertyNames.get(f.predicate) || { alive: "存活", available: "可行动", at: "所在地点", owner: "持有者", knows: "知晓", contains: "记载", relationship: "关系" }[f.predicate] || f.predicate;
    return `${name(f.subject)} · ${predicate}${f.target ? `（${name(f.target)}）` : ""}：${name(f.value)}`;
  }

  function renderCustomEvent(entities) {
    const rule = plan.logic?.custom_rules.find((r) => r.id === plan.change.custom_rule);
    if (!rule) return;
    const bound = { actor: plan.change.character_id, ...plan.change.bindings };
    const substitute = (v) => v.startsWith("$") ? (bound[v.slice(1)] || v) : v;
    const ruleFact = (f) => factText({ ...f, subject: substitute(f.subject), target: substitute(f.target || ""), value: substitute(f.value) }, entities);
    const box = createElement("details", "plot-scene"); box.open = true;
    box.append(createElement("summary", "", `本次自定义事件：${rule.name}`));
    box.append(createElement("p", "", rule.description));
    box.append(createElement("p", "", `需要可行动：${rule.active_roles.map((role) => entities.get(bound[role]) || role).join("、") || "无；按下列前提核验被动参与者"}`));
    for (const f of rule.requires) box.append(createElement("p", "", `前提：${ruleFact(f)}`));
    for (const step of rule.steps) {
      box.append(createElement("p", "", `执行步骤：${entities.get(substitute(step.actor)) || step.actor} · ${labels[step.kind]}`));
      for (const key of Object.keys(fields)) if (step[key]) box.append(createElement("p", "scene-chat-hint", `${fieldLabels[key]}：${valueText(substitute(step[key]), entities)}`));
    }
    for (const f of rule.effects) box.append(createElement("p", "", `结果：${ruleFact(f)}`));
    if (rule.lock_effects) box.append(createElement("p", "scene-chat-hint", "结果将保持锁定，须明确批准新的事件才能更改。"));
    ui.plotPlanPreview.append(box);
  }

  function renderReport(container, report, title, entities) {
    if (!report) return;
    const box = createElement("details", "plot-scene");
    box.append(createElement("summary", "", `${title} · ${report.valid ? "通过" : `${report.issues.length} 项待处理`} · ${report.steps.length} 步`));
    for (const issue of report.issues) {
      box.append(createElement("p", "plot-error", `${issue.scene_id} ${issue.event_id}：${issue.message}`));
      for (const check of issue.checks || []) box.append(createElement("p", "scene-chat-hint", `需要 ${factText(check.expected, entities)}；实际 ${valueText(check.actual, entities)}；来源 ${check.established_by || "未找到"}`));
    }
    const proofs = createElement("details", "");
    proofs.append(createElement("summary", "", "查看事件的前提与结果"));
    for (const step of report.steps) {
      const entry = createElement("details", "plot-scene");
      entry.append(createElement("summary", "", `${step.scene_id} · ${ruleNames.get(step.rule) || labels[step.rule] || step.rule} · ${step.status === "applied" ? "成立" : "被阻止"} (${step.event_id})`));
      for (const check of step.checks) entry.append(createElement("p", "", `前提：${factText(check.expected, entities)}${check.allowed_values?.length ? `（允许：${check.allowed_values.join("、")}）` : ""}；实际 ${valueText(check.actual, entities)}；依据 ${check.established_by || "未找到"}`));
      for (const part of step.substeps || []) entry.append(createElement("p", "scene-chat-hint", `内部步骤 ${labels[part.rule] || part.rule}：${part.status === "applied" ? (step.status === "applied" ? "已提交" : "已撤销") : "失败"}`));
      for (const effect of step.effects) entry.append(createElement("p", "", `结果：${factText(effect, entities)}`));
      proofs.append(entry);
    }
    box.append(proofs);
    container.append(box);
  }

  function renderLogicPlan(entities) {
    logicConfirmed = false;
    logicDirty = false;
    if (!plan.logic) {
      ui.plotPlanPreview.append(createElement("p", "plot-error", "旧计划没有事件账本，请重新预览。"));
      return;
    }
    const facts = createElement("details", "plot-scene");
    facts.open = true;
    facts.append(createElement("summary", "", "确认故事开始前的事实"));
    facts.append(createElement("p", "scene-chat-hint", "以下事实将用于判断行动是否成立。检查是否误用了后续才发生的事情；没有原文确证的内容会标为假设。"));
    const list = createElement("ul", "");
    for (const seed of plan.logic.initial_facts) {
      const item = createElement("li", seed.assumption ? "plot-assumption" : "", `${seed.assumption ? "[假设] " : ""}${factText(seed.fact, entities)} — ${seed.explanation}`);
      for (const evidence of seed.evidence) item.append(createElement("p", "scene-chat-hint", `原始依据 ${evidence.scene_id} 第 ${evidence.element_index + 1} 段：“${evidence.quote}”`));
      list.append(item);
    }
    facts.append(list);
    ui.plotPlanPreview.append(facts);
    if (plan.logic.custom_properties?.length || plan.logic.custom_rules?.length) {
      const definitions = createElement("details", "plot-scene");
      definitions.open = true;
      definitions.append(createElement("summary", "", "确认额外状态与事件规则"));
      for (const prop of plan.logic.custom_properties || []) {
        definitions.append(createElement("p", "", `${prop.name}：${prop.values.join(" / ")}${prop.symmetric ? "；双向更新" : ""}${prop.usable_values.length ? `；仅 ${prop.usable_values.join(" / ")} 时可行动或使用` : ""}`));
      }
      for (const rule of plan.logic.custom_rules || []) {
        const detail = createElement("details", "plot-scene");
        detail.append(createElement("summary", "", rule.name));
        detail.append(createElement("p", "", rule.description));
        detail.append(createElement("p", "", `可行动参与者：${rule.active_roles.join("、") || "无"}`));
        for (const f of rule.requires) detail.append(createElement("p", "", `需要：${factText(f, entities)}`));
        for (const f of rule.effects) detail.append(createElement("p", "", `变为：${factText(f, entities)}`));
        for (const step of rule.steps) detail.append(createElement("p", "", `步骤：${step.actor} · ${labels[step.kind]}`));
        definitions.append(detail);
      }
      ui.plotPlanPreview.append(definitions);
    }
    renderReport(ui.plotPlanPreview, plan.baseline_reasoning, "原稿规则校验", entities);
    renderReport(ui.plotPlanPreview, plan.intervention_reasoning, "变更后的影响推演", entities);
    ui.plotPlanPreview.append(createElement("p", "scene-chat-hint", "影响推演中的后续矛盾是需要改写的内容。原稿账本与本次事件的前提必须先通过校验。"));
    const editor = createElement("details", "plot-scene");
    editor.append(createElement("summary", "", "修正事件账本（高级）"));
    const input = createElement("textarea", "plot-ledger");
    input.setAttribute("aria-label", "事件账本 JSON");
    input.value = JSON.stringify(plan.logic, null, 2);
    const save = createElement("button", "ghost-button", "校验并保存账本");
    save.type = "button";
    const confirm = document.createElement("input");
    confirm.type = "checkbox";
    input.addEventListener("input", () => { logicDirty = true; logicConfirmed = false; confirm.checked = false; buttons(); });
    save.addEventListener("click", async () => {
      if (busy) return;
      busy = true; buttons();
      try {
        const updated = JSON.parse(input.value);
        plan = await api(`plans/${plan.plan_id}/logic`, { logic: updated });
        renderPlan();
        status(ready() ? "账本已保存并校验，请重新确认初始事实。" : "账本已保存，请查看规则校验中的待处理项。", !ready());
      } catch (error) { status(error.message, true); }
      finally { busy = false; buttons(); }
    });
    editor.append(input, save);
    ui.plotPlanPreview.append(editor);
    const consent = createElement("label", "plot-confirm");
    consent.append(confirm, document.createTextNode("我已核对初始事实、假设及自定义规则，允许以此为基础改写。"));
    confirm.addEventListener("change", () => { logicConfirmed = confirm.checked; buttons(); });
    ui.plotPlanPreview.append(consent);
  }

  function sceneText(scene, names) {
    return [scene.heading, `概要：${scene.summary}`, `目标：${scene.goal}`, `冲突：${scene.conflict}`,
      `节拍：${scene.beat}`, "", ...scene.elements.map((e) => e.type === "dialogue"
        ? `${names.get(e.character) || e.character}：${e.text}` : e.text)].join("\n");
  }

  function renderRevision() {
    ui.plotRevisionPreview.replaceChildren();
    if (!revision) return;
    window.Studio?.setCandidate(revision);
    propertyNames = new Map((revision.screenplay.plot_state?.logic?.custom_properties || []).map((p) => [p.id, p.name]));
    ruleNames = new Map((revision.screenplay.plot_state?.logic?.custom_rules || []).map((r) => [`custom:${r.id}`, r.name]));
    const names = characterNameMap(revision.screenplay);
    ui.plotRevisionPreview.append(createElement("h3", "", "候选剧本 · 修改对照"));
    ui.plotRevisionPreview.append(createElement("p", "", `已检查 ${revision.scanned_scene_ids.length} 场，修改 ${revision.changes.length} 场；模型调用 ${revision.calls} 次。${revision.issues.length ? "还有问题，不能采用。" : "检查通过，请预览后采用。"}`));
    for (const issue of revision.issues) {
      ui.plotRevisionPreview.append(createElement("p", "plot-error", `${issue.scene_id}：${issue.message}`));
    }
    renderReport(ui.plotRevisionPreview, revision.reasoning, "候选版本规则校验", new Map((revision.screenplay.plot_state?.logic?.entities || []).map((e) => [e.id, e.name])));
    for (const change of revision.changes) {
      const details = document.createElement("details");
      details.className = "plot-scene";
      details.append(createElement("summary", "", `${change.scene_id} · 查看改写前后`));
      details.append(createElement("p", "", change.reason));
      const compare = createElement("div", "plot-compare");
      for (const [label, scene] of [["改写前", change.before], ["候选版本", change.after]]) {
        const column = document.createElement("div");
        column.append(createElement("h4", "", label), createElement("pre", "", sceneText(scene, names)));
        compare.append(column);
      }
      details.append(compare);
      ui.plotRevisionPreview.append(details);
    }
    versionButtons();
    ui.plotDownloadButton.classList.remove("hidden");
    ui.plotGenerateButton.classList.add("hidden");
  }

  function versionButtons() {
    const adopted = latestScreenplay?.plot_state?.revision_id === revision?.revision_id;
    ui.plotAdoptButton.classList.toggle("hidden", !revision || !!revision.issues.length || adopted);
    ui.plotRollbackButton.classList.toggle("hidden", !revision || !!revision.issues.length || !adopted);
  }

  async function preview() {
    if (!yamlOutput.value.trim()) return status("请先生成或输入剧本。", true);
    if (!ui.plotInstruction.value.trim()) return status("请填写剧情修改要求。", true);
    busy = true;
    buttons();
    baseYaml = yamlOutput.value;
    plannedForm = formSignature();
    const request = { yaml_text: baseYaml, novel_text: novelInput.value, instruction: ui.plotInstruction.value.trim(),
      character_id: ui.plotCharacter.value, scene_id: ui.plotScene.value || currentSceneId,
      operation: ui.plotOperation.value, max_calls: Number(ui.plotMaxCalls.value),
      timeout_seconds: Number(ui.plotTimeout.value), ...eventParameters() };
    plan = null;
    revision = null;
    ui.plotPlanPreview.replaceChildren();
    ui.plotRevisionPreview.replaceChildren();
    [ui.plotGenerateButton, ui.plotAdoptButton, ui.plotRollbackButton, ui.plotDownloadButton, ui.plotResumeButton]
      .forEach((button) => button.classList.add("hidden"));
    status("正在分析角色状态、场景依赖和后续剧情……");
    try {
      plan = await api("plan", request);
      renderPlan();
      status(plan.unresolved_questions.length ? "请处理计划中的待确认事项，再重新预览。" : ready() ? "计划已保存。核对初始事实与影响范围后生成候选版本。" : "计划已保存。请先修正原稿账本或本次事件的前提。");
      if (yamlOutput.value !== baseYaml) status("剧本在规划期间已变化，请重新预览计划。", true);
      if (plannedForm !== formSignature()) {
        ui.plotGenerateButton.classList.add("hidden");
        status("修改要求在规划期间已变化，请重新预览计划。", true);
      }
    } catch (error) { status(error.message, true); }
    finally { busy = false; buttons(); }
  }

  async function poll(id) {
    const generation = ++pollGeneration;
    while (generation === pollGeneration) {
      const job = await api(`jobs/${id}`);
      status(`${job.progress}% · ${job.stage}：${job.message}`, job.status === "failed");
      if (job.status === "succeeded") {
        revision = job.result;
        renderRevision();
        return;
      }
      if (job.status === "failed") {
        ui.plotResumeButton.classList.remove("hidden");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }

  async function generate() {
    if (!plan) return;
    if (plannedForm !== formSignature()) return status("修改要求已变化，请重新预览计划。", true);
    if (!logicConfirmed || logicDirty || !ready()) return status("请先校验账本并确认初始事实。", true);
    busy = true; buttons();
    try {
      const job = await api("jobs", { plan_id: plan.plan_id, yaml_text: yamlOutput.value, confirm_logic: logicConfirmed });
      jobId = job.job_id;
      try { localStorage.setItem(storageKey, JSON.stringify({ jobId, planId: plan.plan_id })); } catch { /* storage optional */ }
      ui.plotGenerateButton.classList.add("hidden");
      await poll(jobId);
    } catch (error) { status(`${error.message} 可用“恢复上次剧情任务”重新连接。`, true); }
    finally { busy = false; buttons(); }
  }

  async function restore() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch { saved = null; }
    if (!saved?.jobId) return status("当前浏览器没有保存的剧情任务。", true);
    busy = true; buttons();
    try {
      const data = await api(`plans/${saved.planId}`);
      plan = data.plan;
      baseYaml = data.base_yaml;
      // Explicit recovery may restore an empty workbench; existing edits are kept for hash checking.
      if (!yamlOutput.value.trim()) showScreenplay(data.base_screenplay, data.base_yaml);
      refreshChoices();
      ui.plotInstruction.value = plan.change.instruction;
      ui.plotCharacter.value = plan.change.character_id;
      ui.plotOperation.value = plan.change.operation;
      for (const [key, id] of Object.entries(fields)) ui[id].value = plan.change[key] || "";
      eventFields();
      ui.plotScene.value = plan.change.effective_scene_id;
      ui.plotMaxCalls.value = plan.max_calls;
      ui.plotTimeout.value = plan.timeout_seconds;
      plannedForm = formSignature();
      renderPlan();
      ui.plotGenerateButton.classList.add("hidden");
      jobId = saved.jobId;
      await poll(jobId);
    } catch (error) { status(error.message, true); }
    finally { busy = false; buttons(); }
  }

  async function switchVersion(action) {
    if (!revision) return;
    const sentYaml = yamlOutput.value;
    busy = true; buttons();
    try {
      const data = await api(`revisions/${revision.revision_id}/${action}`, { yaml_text: sentYaml });
      if (yamlOutput.value !== sentYaml) throw new Error("请求期间剧本发生变化，已保留当前编辑。请重新检查版本。");
      latestReviewReport = null;
      showScreenplay(data.screenplay, data.yaml_text);
      versionButtons();
      status(data.message);
    } catch (error) { status(error.message, true); }
    finally { busy = false; buttons(); }
  }

  ui.plotPlanButton.addEventListener("click", preview);
  ui.plotOperation.addEventListener("change", eventFields);
  eventFields();
  ui.plotGenerateButton.addEventListener("click", generate);
  ui.plotRestoreButton.addEventListener("click", restore);
  ui.plotResumeButton.addEventListener("click", async () => {
    busy = true; buttons();
    try {
      await api(`jobs/${jobId}/resume`, {});
      ui.plotResumeButton.classList.add("hidden");
      await poll(jobId);
    } catch (error) { status(error.message, true); }
    finally { busy = false; buttons(); }
  });
  ui.plotAdoptButton.addEventListener("click", () => switchVersion("adopt"));
  ui.plotRollbackButton.addEventListener("click", () => switchVersion("rollback"));
  ui.plotDownloadButton.addEventListener("click", () => {
    if (!revision) return;
    const url = URL.createObjectURL(new Blob([revision.yaml_text], { type: "text/yaml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${revision.revision_id}.yaml`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
