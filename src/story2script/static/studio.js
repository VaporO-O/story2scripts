/* Authoring UI: model and novel text are always rendered as text, never HTML. */
(() => {
  const $ = (id) => document.getElementById(id);
  const node = (tag, text = "", cls = "") => createElement(tag, cls, text);
  let script = null,
    yaml = "",
    workbenchYaml = "",
    revision = null,
    currentTab = "graph",
    projectionId = 0;
  let draftId = "",
    draftVersion = 0,
    draftBase = "",
    savedHash = "",
    committing = false;
  let sourceEpoch = 0,
    templateList = [],
    templateEnabled = false,
    editorBusy = false;
  let emotions = [],
    emotionHash = "",
    emotionYaml = "",
    speechGeneration = 0,
    utterance = null;
  let voiceChoices = {},
    speechSnapshot = "";
  const queuedSaves = new Set();
  const jobStorageKey = "story2script.studio.lastJob";
  let currentJob = null,
    jobInput = null,
    jobPolling = false,
    jobPollEpoch = 0,
    jobApplied = false;
  async function flushEditors() {
    for (const item of [...queuedSaves])
      if (item.form?.isConnected) await item.flush();
  }
  const status = (text, error = false) => {
    $("studioStatus").textContent = text;
    $("studioStatus").classList.toggle("plot-error", error);
  };
  const names = () => characterNameMap(script || {});
  const editable = () => $("studioSource").value === "current";
  const parseError = (data, fallback) =>
    typeof data.detail === "string" ? data.detail : fallback;

  async function api(path, body, headers = {}) {
    const response = await fetch(
      `/api/studio/${path}`,
      body === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json", ...headers },
            body: JSON.stringify(body),
          },
    );
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(parseError(data, "创作操作失败，请检查输入。"));
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function rememberJob(value) {
    try {
      localStorage.setItem(jobStorageKey, JSON.stringify(value));
    } catch {
      /* history is also on server */
    }
  }
  function jobButtons() {
    const active =
      jobPolling ||
      (currentJob && ["queued", "running"].includes(currentJob.status));
    for (const id of ["structurePropose", "emotionAI", "studioAiCheck"])
      $(id).disabled = !!active;
    $("studioResumeJob").disabled =
      jobPolling ||
      currentJob?.status !== "failed" ||
      currentJob?.stage === "已取消" ||
      currentJob?.attempts >= currentJob?.max_attempts;
    $("studioCancelJob").disabled =
      currentJob?.status !== "queued" || !currentJob?.job_id;
    $("studioRestoreJob").disabled = jobPolling;
    $("studioLoadJobInput").disabled = jobPolling || !jobInput;
    $("studioUseJobResult").disabled =
      jobPolling || !currentJob?.result || jobApplied;
  }
  function paintJob(job) {
    currentJob = job;
    $("studioJobProgress").value = job.progress;
    $("studioJobStatus").textContent =
      `${job.progress}% · ${job.stage}：${job.message} · 调用 ${job.calls}/${job.max_calls} · 累计 ${job.elapsed_seconds}s · 执行 ${job.attempts}/${job.max_attempts} 次`;
    $("studioJobStatus").classList.toggle(
      "plot-error",
      job.status === "failed",
    );
    jobButtons();
  }
  async function pollStudioJob(id) {
    const token = ++jobPollEpoch;
    jobPolling = true;
    jobButtons();
    try {
      while (token === jobPollEpoch) {
        const job = await api(`jobs/${id}`);
        paintJob(job);
        if (job.status === "succeeded") return job.result.payload;
        if (job.status === "failed") throw new Error(job.error || job.message);
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
      throw new Error("已切换到其他任务；原任务仍在后台保存结果。");
    } catch (error) {
      $("studioJobStatus").textContent =
        `${error.message} 可使用“恢复上次创作任务”重新连接，或从任务历史查看。`;
      throw error;
    } finally {
      if (token === jobPollEpoch) {
        jobPolling = false;
        jobButtons();
      }
    }
  }
  async function aiOperation(operation, parameters) {
    if (parameters.mode !== "ai") return api(operation, parameters);
    if (
      jobPolling ||
      (currentJob && ["queued", "running"].includes(currentJob.status))
    )
      throw new Error("当前创作任务尚未结束，请先恢复查询进度。");
    const key = crypto.randomUUID();
    const request = {
      operation,
      parameters,
      max_calls: Number($("studioJobMaxCalls").value),
      timeout_seconds: Number($("studioJobTimeout").value),
    };
    const remembered = { key, jobId: "", operation };
    rememberJob(remembered);
    jobApplied = false;
    jobInput = {
      request,
      screenplay: structuredClone(script),
      yaml_text: parameters.yaml_text,
    };
    currentJob = { status: "queued", job_id: "" };
    jobPolling = true;
    jobButtons();
    $("studioJobStatus").textContent = "正在保存创作任务……";
    try {
      const job = await api("jobs", request, { "Idempotency-Key": key });
      remembered.jobId = job.job_id;
      rememberJob(remembered);
      paintJob(job);
      return await pollStudioJob(job.job_id);
    } catch (error) {
      if ([400, 409, 422].includes(error.status)) currentJob = null;
      $("studioJobStatus").textContent =
        `${error.message}；任务结果不会覆盖工作稿。提交响应丢失时可按上次请求恢复。`;
      throw error;
    } finally {
      jobPolling = false;
      jobButtons();
    }
  }
  async function restoreStudioJob(id = "") {
    if (jobPolling) return;
    try {
      let saved;
      try {
        saved = JSON.parse(localStorage.getItem(jobStorageKey) || "null");
      } catch {
        saved = null;
      }
      if (!id && !saved)
        throw new Error("浏览器没有任务记录，请查看任务历史。");
      const job = await api(
        id || saved?.jobId
          ? `jobs/${id || saved.jobId}`
          : `jobs/requests/${saved.key}`,
      );
      jobInput = await api(`jobs/${job.job_id}/input`);
      jobApplied = false;
      rememberJob({
        key: id ? "" : saved?.key || "",
        jobId: job.job_id,
        operation: jobInput.request.operation,
      });
      paintJob(job);
      if (["queued", "running"].includes(job.status))
        await pollStudioJob(job.job_id);
      if (currentJob?.result)
        $("studioJobStatus").textContent +=
          " · 结果已恢复，请核对原稿后查看或应用。";
    } catch (error) {
      if (error.status === 404 && !id && !currentJob?.job_id) currentJob = null;
      status(error.message, true);
    } finally {
      jobButtons();
    }
  }
  async function applyJobResult() {
    try {
      await flushEditors();
      if (!currentJob?.result || !jobInput) return;
      if (
        !editable() ||
        yamlOutput.value !== yaml ||
        (yaml !== jobInput.yaml_text &&
          yaml !== jobInput.request.parameters.yaml_text)
      ) {
        throw new Error(
          "工作稿与任务原稿不同，结果仍已保存。请先保存当前编辑，再载入任务原稿查看结果。",
        );
      }
      const result = currentJob.result,
        data = result.payload;
      if (result.operation === "structure") {
        $("structureKind").value = jobInput.request.parameters.kind;
        await showTab("editor");
        renderStructureProposal(
          data.screenplay.structure.filter(
            (g) => g.kind === jobInput.request.parameters.kind,
          ),
        );
      } else if (result.operation === "emotions") {
        emotions = data.notes;
        emotionHash = data.hash;
        emotionYaml = yaml;
        await showTab("voices");
        renderEmotion();
      } else {
        accept(data);
        await saveDraft();
        await showTab("editor");
        $("studioChecks").replaceChildren(node("p", data.message));
        for (const issue of [
          ...(data.issues || []),
          ...(data.reasoning?.issues || []),
        ])
          $("studioChecks").append(
            node("p", `${issue.scene_id}：${issue.message}`, "plot-error"),
          );
      }
      jobApplied = true;
      jobButtons();
      status("任务结果已载入；分组仍需预览确认后应用。");
    } catch (error) {
      status(error.message, true);
    }
  }
  $("studioRestoreJob").onclick = () => restoreStudioJob();
  $("studioResumeJob").onclick = async () => {
    if (!currentJob?.job_id || jobPolling) return;
    const id = currentJob.job_id;
    jobPolling = true;
    jobButtons();
    try {
      paintJob(await api(`jobs/${id}/resume`, {}));
      await pollStudioJob(id);
    } catch (error) {
      status(error.message, true);
    } finally {
      jobPolling = false;
      jobButtons();
    }
  };
  $("studioCancelJob").onclick = async () => {
    try {
      paintJob(await api(`jobs/${currentJob.job_id}/cancel`, {}));
    } catch (error) {
      status(error.message, true);
    }
  };
  $("studioLoadJobInput").onclick = async () => {
    try {
      if (!jobInput) return;
      await flushEditors();
      if (yamlOutput.value.trim() && yamlOutput.value !== workbenchYaml)
        throw new Error("源码有未校验编辑，请先处理后再载入任务原稿。");
      if (latestScreenplay) {
        $("studioSource").value = "current";
        syncFromWorkbench();
        await saveDraft();
      }
      draftId = "";
      draftVersion = 0;
      draftBase = jobInput.yaml_text;
      $("studioSource").value = "current";
      accept(jobInput);
      status("已载入任务原稿，原有工作稿已保存。");
    } catch (error) {
      status(error.message, true);
    }
  };
  $("studioUseJobResult").onclick = applyJobResult;
  $("studioListJobs").onclick = async () => {
    try {
      const data = await api("jobs");
      const box = $("studioJobHistory");
      box.replaceChildren();
      for (const job of data.jobs) {
        const b = node(
          "button",
          `${job.stage} · ${job.progress}% · ${new Date(job.created_at).toLocaleString()} · ${job.job_id.slice(0, 8)}`,
          "text-button",
        );
        b.type = "button";
        b.onclick = () => restoreStudioJob(job.job_id);
        box.append(b);
      }
      if (!data.jobs.length) box.append(node("p", "尚无创作后台任务。"));
    } catch (error) {
      status(error.message, true);
    }
  };

  function sceneOptions() {
    const selected = $("studioScene").value || currentSceneId;
    $("studioScene").replaceChildren(
      ...(script?.scenes || []).map(
        (s, i) => new Option(`${i + 1} · ${s.location} · ${s.beat}`, s.id),
      ),
    );
    $("studioScene").value = script?.scenes.some((s) => s.id === selected)
      ? selected
      : script?.scenes[0]?.id || "";
    const character = $("studioCharacter").value;
    $("studioCharacter").replaceChildren(
      new Option("全部角色", ""),
      ...(script?.characters || []).map((c) => new Option(c.name, c.id)),
    );
    if (script?.characters.some((c) => c.id === character))
      $("studioCharacter").value = character;
  }

  function stopSpeech(note = "朗读已停止。") {
    speechGeneration += 1;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    utterance = null;
    $("speechCurrent").textContent = note;
  }

  function accept(result, preserveEditor = false) {
    script = result.screenplay;
    yaml = result.yaml_text;
    workbenchYaml = yaml;
    sourceEpoch += 1;
    emotionHash = "";
    emotionYaml = "";
    emotions = [];
    stopSpeech("剧本已更新，朗读已停止。请重新开始。");
    committing = true;
    latestReviewReport = null;
    if (preserveEditor) {
      latestScreenplay = script;
      yamlOutput.value = yaml;
      updateScreenplayStatus();
    } else showScreenplay(script, yaml);
    committing = false;
    sceneOptions();
    renderTree();
    if (!preserveEditor) renderEditor();
    renderEmotion();
    renderVoices();
    if (currentTab === "graph") refreshProjection();
  }

  function syncFromWorkbench() {
    if (!latestScreenplay || !yamlOutput.value.trim())
      throw new Error("请先生成或校验剧本。");
    if (workbenchYaml && yamlOutput.value !== workbenchYaml)
      throw new Error("工作台源码有未校验变化，请先校验，防止使用过期剧本。");
    script = latestScreenplay;
    yaml = yamlOutput.value;
    sceneOptions();
    renderTree();
    renderEditor();
    renderVoices();
    renderEmotion();
  }

  async function selectVersion() {
    try {
      await flushEditors();
    } catch (e) {
      $("studioSource").value = "current";
      status(e.message, true);
      return;
    }
    sourceEpoch += 1;
    stopSpeech();
    const source = $("studioSource").value;
    if (source === "current") {
      if (!latestScreenplay) return status("请先生成或校验剧本。", true);
      script = latestScreenplay;
      yaml = yamlOutput.value;
    } else if (revision) {
      script =
        source === "candidate" ? revision.screenplay : revision.base_screenplay;
      yaml = source === "candidate" ? revision.yaml_text : "";
      // Base YAML comes from the persisted plan, not an ad-hoc serializer.
      if (source === "base") {
        const epoch = sourceEpoch;
        fetch(`/api/plot/plans/${revision.plan_id}`)
          .then((r) => r.json())
          .then((data) => {
            if (epoch !== sourceEpoch) return;
            if (!data.base_yaml) throw new Error("无法载入候选原稿。");
            yaml = data.base_yaml;
            refreshProjection();
          })
          .catch((e) => status(e.message, true));
      }
    }
    emotions = [];
    emotionYaml = "";
    sceneOptions();
    renderTree();
    renderEditor();
    renderVoices();
    renderEmotion();
    status(
      `${source === "current" ? "当前工作稿" : source === "candidate" ? "候选版本（只读）" : "候选原稿（只读）"} · ${script?.title || ""}${script?.editing?.status === "needs_check" ? " · 编辑后待核验" : ""}`,
    );
    if (yaml) refreshProjection();
  }

  async function showTab(tab) {
    try {
      await flushEditors();
    } catch (e) {
      status(e.message, true);
      return;
    }
    currentTab = tab;
    document
      .querySelectorAll("[data-studio-panel]")
      .forEach((p) =>
        p.classList.toggle("hidden", p.dataset.studioPanel !== tab),
      );
    document
      .querySelectorAll("[data-studio-tab]")
      .forEach((b) =>
        b.classList.toggle("is-active", b.dataset.studioTab === tab),
      );
    if (tab === "graph") refreshProjection();
    if (tab === "editor") {
      renderTree();
      renderEditor();
    }
    if (tab === "voices") {
      renderVoices();
      if (!emotions.length) readEmotions("annotated");
    }
    if (tab === "files") listDrafts();
  }

  function selectedScene() {
    return script?.scenes.find((s) => s.id === $("studioScene").value);
  }

  function jump(sid, index = null, quote = "") {
    if (!script?.scenes.some((s) => s.id === sid)) return;
    $("studioScene").value = sid;
    if (editable()) setCurrentScene(sid);
    renderEditor();
    renderTree();
    const box = $("studioEvidence");
    box.replaceChildren();
    const scene = script.scenes.find((s) => s.id === sid);
    box.append(node("h3", `${sid} · ${scene.heading}`));
    if (index !== null) {
      box.append(node("p", `第 ${index + 1} 段 · 当前版本正文`));
      box.append(
        node("blockquote", scene.elements[index]?.text || "正文位置已不存在"),
      );
      if (quote && !scene.elements[index]?.text.includes(quote))
        box.append(node("p", `保存的原始依据：“${quote}”`, "plot-error"));
    } else box.append(node("p", scene.summary));
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function evidenceButton(ev, label = "查看正文依据") {
    const b = node("button", label, "text-button");
    b.type = "button";
    b.addEventListener("click", () =>
      jump(ev.scene_id, ev.element_index, ev.quote),
    );
    return b;
  }

  function showSource(source, data) {
    const box = $("studioEvidence");
    box.replaceChildren(node("h3", "状态来源"));
    const entry = data.sources[source];
    if (!entry) {
      box.append(node("p", "没有可用的来源记录。"));
      return;
    }
    box.append(
      node(
        "p",
        `${source} · ${entry.type === "assumption" ? "已确认初始假设" : "证据记录"}`,
      ),
    );
    if (entry.explanation) box.append(node("p", entry.explanation));
    for (const ev of entry.evidence || [])
      box.append(
        node("blockquote", ev.quote),
        evidenceButton(ev, `${ev.scene_id} 第 ${ev.element_index + 1} 段`),
      );
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const predicateName = (p) =>
    ({
      alive: "存活",
      available: "可行动",
      at: "地点",
      owner: "持有人",
      knows: "知晓",
      contains: "记载",
      relationship: "关系",
    })[p] ||
    script?.plot_state?.logic?.custom_properties.find((d) => d.id === p)
      ?.name ||
    p;
  const valueName = (v) =>
    ({ true: "是", false: "否", ally: "同盟", enemy: "敌对", neutral: "中立" })[
      v
    ] ||
    script?.plot_state?.logic?.entities.find((e) => e.id === v)?.name ||
    names().get(v) ||
    v ||
    "未知";
  const factText = (f) =>
    `${valueName(f.subject)} · ${predicateName(f.predicate)}${f.target ? `（${valueName(f.target)}）` : ""}：${valueName(f.value)}`;

  async function refreshProjection() {
    if (!script || !yaml) return;
    const token = ++projectionId,
      sent = yaml;
    try {
      const data = await api("projection", {
        yaml_text: sent,
        scene_id: $("studioScene").value,
        boundary: $("studioBoundary").value,
      });
      if (token !== projectionId || yaml !== sent) return;
      status(
        `${$("studioSource").selectedOptions[0].text} · ${data.scene_id} · ${data.note}`,
        data.stale || data.issues.length > 0,
      );
      renderGraph(data);
      renderTimeline(data);
    } catch (e) {
      if (token === projectionId) status(e.message, true);
    }
  }

  function svgNode(tag, attrs = {}, text = "") {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    if (text) n.textContent = text;
    return n;
  }

  function renderGraph(data) {
    const container = $("studioGraph");
    container.replaceChildren();
    const svg = svgNode("svg", {
      viewBox: "0 0 680 460",
      role: "img",
      "aria-label": "所选场景时点的人物关系图",
    });
    const defs = svgNode("defs"),
      marker = svgNode("marker", {
        id: "studioArrow",
        viewBox: "0 0 10 10",
        refX: 26,
        refY: 5,
        markerWidth: 7,
        markerHeight: 7,
        orient: "auto-start-reverse",
      });
    marker.append(
      svgNode("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#4169a1" }),
    );
    defs.append(marker);
    svg.append(defs);
    const visible = data.characters.filter(
      (c) => c.scene_ids.length || c.facts.length,
    );
    if (!visible.length) {
      container.append(node("p", "该时点尚无人物出场或已知状态。"));
      return;
    }
    const positions = new Map(
      visible.map((c, i) => [
        c.id,
        {
          x: 340 + 240 * Math.cos((i * Math.PI * 2) / visible.length),
          y: 230 + 165 * Math.sin((i * Math.PI * 2) / visible.length),
        },
      ]),
    );
    const edges = [];
    for (const relation of data.relationships) {
      if (!positions.has(relation.from) || !positions.has(relation.to))
        continue;
      const g = svgNode("g", {
        tabindex: "0",
        role: "button",
        "aria-label": `${valueName(relation.from)} 与 ${valueName(relation.to)}：${valueName(relation.label)}`,
      });
      const line = svgNode("line", {
        stroke: relation.kind === "fact" ? "#4169a1" : "#aeb9c9",
        "stroke-width": 2,
        "stroke-dasharray": relation.kind === "fact" ? "" : "5 5",
      });
      if (relation.kind === "fact" && !relation.symmetric)
        line.setAttribute("marker-end", "url(#studioArrow)");
      const label = svgNode(
        "text",
        {
          "text-anchor": "middle",
          "font-size": 13,
          fill: "#334155",
          "paint-order": "stroke",
          stroke: "white",
          "stroke-width": 4,
        },
        valueName(relation.label),
      );
      g.append(line, label);
      svg.append(g);
      edges.push({ relation, line, label });
      const inspect = () =>
        relation.source
          ? showSource(relation.source, data)
          : status(
              "虚线只表示在此前场景共同出场，不代表友情、敌意或已确认关系。",
            );
      g.addEventListener("click", inspect);
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter") inspect();
      });
    }
    const circles = [];
    for (const c of visible) {
      const p = positions.get(c.id),
        g = svgNode("g", {
          transform: `translate(${p.x} ${p.y})`,
          tabindex: 0,
          role: "button",
          "aria-label": `选择${c.name}`,
        });
      const unavailable = c.facts.some(
        (f) =>
          ["alive", "available"].includes(f.predicate) && f.value === "false",
      );
      g.append(
        svgNode("circle", {
          r: 29,
          fill: unavailable ? "#e2e8f0" : "#dbeafe",
          stroke: $("studioCharacter").value === c.id ? "#1d4ed8" : "#93b2d4",
          "stroke-width": 3,
        }),
        svgNode(
          "text",
          { "text-anchor": "middle", y: 5, "font-size": 14, fill: "#172033" },
          c.name,
        ),
      );
      let drag = null,
        moved = false;
      g.addEventListener("pointerdown", (e) => {
        drag = [e.clientX, e.clientY, p.x, p.y];
        moved = false;
        g.setPointerCapture(e.pointerId);
      });
      g.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const box = svg.getBoundingClientRect();
        const dx = ((e.clientX - drag[0]) * 680) / zoom / box.width,
          dy = ((e.clientY - drag[1]) * 460) / zoom / box.height;
        moved ||= Math.abs(dx) + Math.abs(dy) > 4;
        p.x = Math.max(35, Math.min(645, drag[2] + dx));
        p.y = Math.max(35, Math.min(425, drag[3] + dy));
        g.setAttribute("transform", `translate(${p.x} ${p.y})`);
        updateEdges();
      });
      g.addEventListener("pointerup", () => {
        drag = null;
      });
      const choose = () => {
        $("studioCharacter").value = c.id;
        renderFacts(c);
        renderTree();
        renderEmotion();
      };
      g.addEventListener("click", () => {
        if (!moved) choose();
      });
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter") choose();
      });
      svg.append(g);
      circles.push(g);
    }
    function updateEdges() {
      for (const { relation, line, label } of edges) {
        const a = positions.get(relation.from),
          b = positions.get(relation.to);
        for (const [k, v] of Object.entries({
          x1: a.x,
          y1: a.y,
          x2: b.x,
          y2: b.y,
        }))
          line.setAttribute(k, v);
        label.setAttribute("x", (a.x + b.x) / 2);
        label.setAttribute("y", (a.y + b.y) / 2 - 6);
      }
    }
    updateEdges();
    container.append(svg);
    let zoom = 1;
    const zoomControls = node("div", "", "button-row");
    for (const [label, factor] of [
      ["放大", 1.25],
      ["缩小", 0.8],
      ["重置视图", 0],
    ]) {
      const b = node("button", label, "ghost-button");
      b.type = "button";
      b.onclick = () => {
        zoom = factor ? Math.min(2.5, Math.max(0.7, zoom * factor)) : 1;
        svg.setAttribute(
          "viewBox",
          `${340 - 340 / zoom} ${230 - 230 / zoom} ${680 / zoom} ${460 / zoom}`,
        );
      };
      zoomControls.append(b);
    }
    container.append(zoomControls);
    const list = node("div", "", "button-row");
    for (const c of visible) {
      const b = node("button", c.name, "ghost-button");
      b.type = "button";
      b.onclick = () => {
        $("studioCharacter").value = c.id;
        renderFacts(c);
        renderTree();
        renderEmotion();
      };
      list.append(b);
    }
    container.append(list);
    for (const r of data.relationships.filter((r) => r.kind === "fact")) {
      const b = node(
        "button",
        `${valueName(r.from)} ${r.symmetric ? "↔" : "→"} ${valueName(r.to)}：${valueName(r.label)} · 查看来源`,
        "text-button",
      );
      b.onclick = () => showSource(r.source, data);
      container.append(b);
    }
    const selected = data.characters.find(
      (c) => c.id === $("studioCharacter").value,
    );
    if (selected) renderFacts(selected);
    else
      $("studioCharacterFacts").replaceChildren(
        node("p", "点击角色查看此时状态与涉及场景。"),
      );
    function renderFacts(c) {
      const box = $("studioCharacterFacts");
      box.replaceChildren(node("h4", c.name));
      if (!c.facts.length) box.append(node("p", "当前没有可确认的状态事实。"));
      for (const f of c.facts) box.append(node("p", factText(f)));
      for (const sid of c.scene_ids) {
        const b = node("button", sid, "text-button");
        b.type = "button";
        b.onclick = () => jump(sid);
        box.append(b);
      }
    }
  }

  function renderTimeline(data) {
    const box = $("studioTimeline");
    box.replaceChildren();
    if (!data.has_logic)
      box.append(
        node(
          "p",
          "尚无事件账本，以下为场景摘要。通过剧情改写预览可建立可追溯事件。",
        ),
      );
    for (const scene of data.scenes) {
      const row = node("details", "", "timeline-scene");
      row.open = scene.id === data.scene_id;
      row.append(
        node(
          "summary",
          `${scene.id} · ${scene.timeline_mode === "present" ? "当下" : `历史 → ${scene.event_time_scene_id}`}`,
        ),
        node("p", scene.summary),
      );
      const deps = data.dependencies[scene.id] || [];
      if (deps.length)
        row.append(
          node("p", `前提来源场景：${deps.join("、")}`, "scene-chat-hint"),
        );
      for (const event of data.events.filter((e) => e.scene_id === scene.id)) {
        const detail = node("details", "", "timeline-event");
        detail.append(
          node(
            "summary",
            `${event.label} · ${event.status === "applied" ? "成立" : "前提不足"}`,
          ),
        );
        for (const check of event.checks) {
          const p = node(
            "p",
            `需要 ${factText(check.expected)}；实际 ${valueName(check.actual)}`,
          );
          if (check.established_by) {
            const b = node("button", "追溯来源", "text-button");
            b.onclick = () => showSource(check.established_by, data);
            p.append(b);
          }
          detail.append(p);
        }
        for (const f of event.effects)
          detail.append(node("p", `结果 ${factText(f)}`));
        for (const ev of event.evidence) detail.append(evidenceButton(ev));
        row.append(detail);
      }
      box.append(row);
    }
    for (const issue of data.issues)
      box.append(
        node("p", `${issue.scene_id}：${issue.message}`, "plot-error"),
      );
  }

  function renderTree() {
    const box = $("studioSceneTree");
    box.replaceChildren();
    if (!script) return;
    const kind = $("structureKind").value;
    let groups = (script.structure || []).filter((g) => g.kind === kind);
    if (!groups.length)
      groups = [
        {
          id: "all",
          title: "全部场景（尚未分组）",
          scene_ids: script.scenes.map((s) => s.id),
        },
      ];
    for (const group of groups) {
      const item = node("details", "", "scene-tree-group");
      item.open = true;
      item.append(
        node(
          "summary",
          group.title +
            (group.target_minutes
              ? ` · 目标 ${group.target_minutes} 分钟`
              : ""),
        ),
      );
      for (const id of group.scene_ids) {
        const s = script.scenes.find((v) => v.id === id),
          c = $("studioCharacter").value;
        if (
          c &&
          !s.characters_present.includes(c) &&
          !s.elements.some((e) => e.type === "dialogue" && e.character === c)
        )
          continue;
        const b = node(
          "button",
          `${id} · ${s.location} · ${s.beat}`,
          `scene-tree-button ${id === $("studioScene").value ? "is-active" : ""}`,
        );
        b.type = "button";
        b.onclick = async () => {
          try {
            await flushEditors();
          } catch (e) {
            status(e.message, true);
            return;
          }
          $("studioScene").value = id;
          if (editable()) setCurrentScene(id);
          renderTree();
          renderEditor();
        };
        item.append(b);
      }
      box.append(item);
    }
    if (editable() && groups[0]?.id !== "all") {
      const b = node("button", "调整标题与分界", "ghost-button");
      b.onclick = () => renderStructureProposal(groups);
      box.append(b);
    }
  }

  function renderStructureProposal(groups) {
    const box = $("structureProposal");
    box.replaceChildren(node("h4", "分组预览"));
    const rows = [];
    for (const group of groups) {
      const row = node("div", "", "structure-row"),
        title = node("input"),
        end = node("select"),
        minutes = node("input");
      title.value = group.title;
      title.maxLength = 100;
      title.setAttribute("aria-label", `${group.id} 标题`);
      end.setAttribute("aria-label", `${group.id} 结束场景`);
      for (const s of script.scenes)
        end.add(new Option(`${s.id} · ${s.location}`, s.id));
      end.value = group.scene_ids.at(-1);
      minutes.type = "number";
      minutes.min = "0.1";
      minutes.max = "300";
      minutes.step = "0.1";
      minutes.placeholder = "目标分钟";
      minutes.value = group.target_minutes || "";
      minutes.setAttribute("aria-label", `${group.id} 目标分钟`);
      row.append(title, end, minutes);
      rows.push({ group, title, end, minutes });
      box.append(row);
    }
    const apply = node("button", "应用分组并保存草稿", "primary-button");
    apply.type = "button";
    box.append(apply);
    apply.onclick = async () => {
      if (!editable())
        return status("候选与原稿只读，请切回当前工作稿。", true);
      let start = 0;
      const proposed = rows.map(({ group, title, end, minutes }) => {
        const last = script.scenes.findIndex((s) => s.id === end.value);
        const ids = script.scenes.slice(start, last + 1).map((s) => s.id);
        start = last + 1;
        return {
          ...group,
          title: title.value.trim(),
          scene_ids: ids,
          target_minutes: minutes.value ? Number(minutes.value) : null,
        };
      });
      try {
        const result = await safeEdit({
          structure: [
            ...(script.structure || []).filter(
              (g) => g.kind !== groups[0].kind,
            ),
            ...proposed,
          ],
        });
        accept(result);
        await saveDraft();
        box.replaceChildren();
        status("分组已保存，场景顺序和 ID 保持一致。");
      } catch (e) {
        status(e.message, true);
      }
    };
  }

  async function safeEdit(update) {
    if (!editable()) throw new Error("该版本只读，请切回当前工作稿。");
    if (!script || yamlOutput.value !== yaml)
      throw new Error("工作台源码已变化，请先校验并读取最新剧本。");
    const sent = yaml,
      epoch = sourceEpoch;
    const result = await api("edit", { yaml_text: sent, ...update });
    if (epoch !== sourceEpoch || yamlOutput.value !== sent)
      throw new Error("请求期间剧本有新变化，保留当前编辑，请重试。");
    return result;
  }

  function sceneEditor(scene, host) {
    host.replaceChildren();
    if (!scene) return;
    const form = node("form", "", "inline-scene-editor"),
      controls = {};
    const field = (label, value, tag = "input") => {
      const wrap = node("label", label),
        input = node(tag);
      input.value = value;
      wrap.append(input);
      form.append(wrap);
      return input;
    };
    for (const key of [
      "location",
      "summary",
      "goal",
      "conflict",
      "beat",
      "subtext",
    ])
      controls[key] = field(
        {
          location: "地点（自动更新场景标题）",
          summary: "场景概要",
          goal: "目标",
          conflict: "冲突",
          beat: "节拍",
          subtext: "潜台词",
        }[key],
        scene[key],
        key === "location" ? "input" : "textarea",
      );
    controls.int_ext = field("内外景", "", "select");
    for (const v of ["INT.", "EXT."]) controls.int_ext.add(new Option(v, v));
    controls.int_ext.value = scene.int_ext;
    controls.time_of_day = field("时间", "", "select");
    for (const v of ["DAY", "NIGHT"])
      controls.time_of_day.add(new Option(v, v));
    controls.time_of_day.value = scene.time_of_day;
    const elementFields = [];
    scene.elements.forEach((e, i) => {
      const section = node("fieldset");
      section.append(
        node(
          "legend",
          `第 ${i + 1} 段 · ${e.type === "dialogue" ? "对白" : "动作"}`,
        ),
      );
      const text = node("textarea");
      text.value = e.text;
      text.setAttribute("aria-label", `${scene.id} 第 ${i + 1} 段正文`);
      section.append(text);
      const fields = { text };
      if (e.type === "dialogue") {
        const speaker = node("select");
        speaker.setAttribute("aria-label", `第 ${i + 1} 段说话人`);
        for (const c of script.characters)
          speaker.add(new Option(c.name, c.id));
        speaker.value = e.character;
        fields.character = speaker;
        section.append(speaker);
        for (const k of ["parenthetical", "emotion"]) {
          const label = node(
              "label",
              k === "emotion" ? "情绪标注" : "表演指示",
            ),
            input = node("input");
          input.value = e[k] || "";
          label.append(input);
          section.append(label);
          fields[k] = input;
        }
      }
      elementFields.push(fields);
      form.append(section);
    });
    const line = node(
        "p",
        "编辑后将保存草稿，并重新核对剧情。",
        "scene-chat-hint",
      ),
      save = node("button", "立即保存场景", "primary-button");
    save.type = "submit";
    form.append(line, save);
    host.append(form);
    const startingEpoch = sourceEpoch;
    let timer = null,
      saving = false,
      pending = false,
      closed = false,
      running = null,
      needsPersist = false;
    if (!editable()) {
      form
        .querySelectorAll("input,textarea,select,button")
        .forEach((e) => (e.disabled = true));
      line.textContent = "此版本只读；切换到当前工作稿即可编辑。";
      return;
    }
    function collect() {
      const replacement = structuredClone(scene);
      for (const [k, input] of Object.entries(controls))
        replacement[k] = input.value.trim();
      replacement.heading = `${replacement.int_ext} ${replacement.location} - ${replacement.time_of_day}`;
      replacement.elements = scene.elements.map((e, i) => ({
        ...e,
        ...Object.fromEntries(
          Object.entries(elementFields[i]).map(([k, input]) => [
            k,
            input.value,
          ]),
        ),
      }));
      const speakers = replacement.elements
        .filter((e) => e.type === "dialogue")
        .map((e) => e.character);
      replacement.characters = [
        ...new Set([...replacement.characters, ...speakers]),
      ];
      replacement.characters_present = [
        ...new Set([...replacement.characters_present, ...speakers]),
      ];
      return replacement;
    }
    let last = JSON.stringify(collect()),
      localEpoch = startingEpoch;
    async function flush() {
      if (closed || !form.isConnected) return;
      if (saving) {
        pending = true;
        await running;
        return flush();
      }
      const replacement = collect(),
        signature = JSON.stringify(replacement);
      if (signature === last && !needsPersist) return;
      if (sourceEpoch !== localEpoch) {
        line.textContent =
          "剧本已在别处更新，此处编辑仍保留，请重新打开场景后合并。";
        throw new Error(line.textContent);
      }
      saving = true;
      editorBusy = true;
      save.disabled = true;
      line.textContent = "正在保存草稿……";
      running = (async () => {
        try {
          if (signature !== last) {
            const result = await safeEdit({ scene: replacement });
            accept(result, true);
            localEpoch = sourceEpoch;
            last = signature;
            needsPersist = true;
          }
          await saveDraft();
          needsPersist = false;
          localEpoch = sourceEpoch;
          line.textContent = "已保存草稿 · 正文变化待重新核验。";
        } catch (e) {
          line.textContent = e.message;
          status(e.message, true);
          throw e;
        } finally {
          saving = false;
          editorBusy = false;
          save.disabled = false;
        }
      })();
      await running;
      if (pending) {
        pending = false;
        return flush();
      }
    }
    form.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => flush().catch(() => {}), 2000);
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      clearTimeout(timer);
      flush().catch(() => {});
    });
    const handle = {
      flush,
      dirty: () => JSON.stringify(collect()) !== last || saving || needsPersist,
      close: () => {
        closed = true;
        clearTimeout(timer);
        queuedSaves.delete(handle);
      },
    };
    queuedSaves.add(handle);
    // Do not overwrite an active form on autosave. Disconnected forms are pruned on the next edit.
    for (const old of [...queuedSaves])
      if (old !== handle && old.form && !old.form.isConnected) old.close();
    handle.form = form;
  }

  function renderEditor() {
    sceneEditor(selectedScene(), $("studioSceneEditor"));
  }

  async function saveDraft(separate = false) {
    if (!editable() || !script) return;
    const sent = yaml,
      epoch = sourceEpoch;
    const result = await api("drafts", {
      screenplay: script,
      draft_id: separate ? "" : draftId,
      expected_version: separate ? 0 : draftVersion,
      base_yaml: draftBase || sent,
    });
    if (epoch !== sourceEpoch || sent !== yaml)
      throw new Error(
        "草稿快照已保存，但工作区已有新编辑，请再次保存当前内容。",
      );
    draftId = result.draft_id;
    draftVersion = result.version;
    savedHash = result.hash;
    draftBase = result.yaml_text;
    if (result.yaml_text !== yaml) {
      script = result.screenplay;
      yaml = result.yaml_text;
      workbenchYaml = yaml;
      latestScreenplay = script;
      yamlOutput.value = yaml;
      updateScreenplayStatus();
      sourceEpoch += 1;
    }
    try {
      localStorage.setItem("story2script.studio.lastDraft", draftId);
    } catch {
      /* optional */
    }
    $("draftStatus").textContent =
      `已保存《${script.title}》草稿第 ${draftVersion} 版 · ${new Date(result.saved_at).toLocaleString()}`;
    return result;
  }

  async function listDrafts() {
    try {
      const data = await api("drafts");
      const box = $("studioDrafts");
      box.replaceChildren();
      for (const entry of data.drafts) {
        const row = node("details", "", "draft-entry");
        row.append(
          node(
            "summary",
            `${entry.title} · 第 ${entry.version} 版 · ${new Date(entry.saved_at).toLocaleString()}`,
          ),
        );
        const load = node("button", "载入最新草稿", "ghost-button"),
          history = node("button", "查看历史", "ghost-button");
        load.onclick = () => loadDraft(entry.draft_id);
        history.onclick = async () => {
          try {
            const result = await api(`drafts/${entry.draft_id}/versions`);
            history.disabled = true;
            for (const v of result.versions) {
              const b = node(
                "button",
                `载入第 ${v.version} 版 · ${new Date(v.saved_at).toLocaleString()}`,
                "text-button",
              );
              b.onclick = () =>
                loadDraft(entry.draft_id, v.version, entry.version);
              row.append(b);
            }
          } catch (e) {
            status(e.message, true);
          }
        };
        row.append(load, history);
        box.append(row);
      }
      if (!data.drafts.length) box.append(node("p", "还没有保存的草稿。"));
    } catch (e) {
      status(e.message, true);
    }
  }

  async function loadDraft(id, version = null, headVersion = null) {
    try {
      if (editorBusy) throw new Error("请等待当前编辑保存完成。");
      await Promise.all(
        [...queuedSaves]
          .filter((s) => s.form?.isConnected)
          .map((s) => s.flush()),
      );
      if (
        editable() &&
        script &&
        (draftId !== id || yamlOutput.value !== yaml)
      ) {
        if (yamlOutput.value !== yaml)
          throw new Error(
            "源码含未校验修改，请先校验并保存，防止载入草稿覆盖编辑。",
          );
        await saveDraft();
      }
      const data = await api(
        `drafts/${id}${version ? `?version=${version}` : ""}`,
      );
      draftId = version ? "" : id;
      draftVersion = version ? 0 : data.version;
      draftBase = data.yaml_text;
      savedHash = data.hash;
      $("studioSource").value = "current";
      accept(data);
      $("draftStatus").textContent = version
        ? `已载入历史第 ${version} 版；下次保存为独立草稿，原历史不覆盖。`
        : `已载入草稿第 ${data.version} 版。`;
      status("草稿已载入工作台。");
      void headVersion;
    } catch (e) {
      status(e.message, true);
    }
  }

  function creativeBrief() {
    if (!templateEnabled) return null;
    const selected = templateList.find(
      (b) =>
        `${b.template_id}:${b.template_version}` ===
        $("creativeTemplate").value,
    );
    return {
      template_id: selected?.template_id || "custom",
      template_version: selected?.template_version || 1,
      name: $("creativeName").value.trim() || "我的改编风格",
      tone: $("creativeTone").value,
      pacing: $("creativePacing").value,
      fidelity: $("creativeFidelity").value,
      instructions: $("creativeInstructions").value,
    };
  }
  async function loadTemplates() {
    try {
      const data = await api("templates");
      templateList = data.templates;
      const chosen = $("creativeTemplate").value;
      $("creativeTemplate").replaceChildren(
        new Option("沿用默认改编风格", ""),
        ...templateList.map(
          (b) =>
            new Option(
              `${b.name} · v${b.template_version}`,
              `${b.template_id}:${b.template_version}`,
            ),
        ),
      );
      $("creativeTemplate").value = chosen;
    } catch (e) {
      $("creativeStatus").textContent = e.message;
    }
  }

  async function readEmotions(mode) {
    try {
      await flushEditors();
    } catch (e) {
      status(e.message, true);
      return;
    }
    if (!script || !yaml) return;
    const sent = yaml,
      epoch = sourceEpoch;
    $("emotionEvidence").textContent =
      mode === "ai" ? "正在分析当前场景的对白情绪……" : "正在读取情绪标注……";
    try {
      const data = await aiOperation("emotions", {
        yaml_text: sent,
        mode,
        scene_ids: mode === "ai" ? [$("studioScene").value] : [],
      });
      if (
        epoch !== sourceEpoch ||
        sent !== yaml ||
        (editable() && yamlOutput.value !== sent)
      ) {
        status(
          "分析期间剧本已变化；后台结果已保存，可从任务面板恢复查看。",
          true,
        );
        return;
      }
      emotions = data.notes;
      emotionHash = data.hash;
      emotionYaml = sent;
      renderEmotion();
    } catch (e) {
      $("emotionEvidence").textContent = e.message;
    }
  }
  function renderEmotion() {
    const box = $("emotionTrajectory");
    box.replaceChildren();
    $("emotionEvidence").replaceChildren();
    if (!script || !emotions.length || (emotionYaml && emotionYaml !== yaml)) {
      box.append(node("p", "暂无有效情绪轨迹；可读取已有标注或分析当前场景。"));
      return;
    }
    const character = $("studioCharacter").value,
      shown = emotions.filter(
        (n) => !character || n.character_id === character,
      );
    const table = node("table", "", "emotion-table"),
      head = node("tr");
    for (const label of ["角色", "场景", "情绪", "依据"])
      head.append(node("th", label));
    const thead = node("thead");
    thead.append(head);
    table.append(thead);
    const body = node("tbody");
    for (const n of shown) {
      const row = node("tr");
      row.append(node("td", valueName(n.character_id)), node("td", n.scene_id));
      const mood = node("td"),
        pill = node(
          "span",
          n.emotion,
          `emotion-pill ${n.source === "unknown" ? "unknown" : ""}`,
        );
      mood.append(
        pill,
        node(
          "small",
          n.source === "ai"
            ? " 模型解释"
            : n.source === "unknown"
              ? " 无标注"
              : " 正文标注",
        ),
      );
      row.append(mood);
      const ev = node("td"),
        b = node(
          "button",
          n.quote.length > 65 ? n.quote.slice(0, 65) + "…" : n.quote,
          "text-button",
        );
      b.onclick = () => {
        $("emotionEvidence").replaceChildren(
          node("blockquote", n.quote),
          node("p", n.rationale || "没有额外表演指示。"),
        );
        $("studioScene").value = n.scene_id;
      };
      ev.append(b);
      row.append(ev);
      body.append(row);
    }
    table.append(body);
    box.append(table);
    const groups = new Map();
    for (const n of shown) {
      if (!groups.has(n.character_id)) groups.set(n.character_id, []);
      groups.get(n.character_id).push(n);
    }
    for (const [cid, notes] of groups) {
      const line = node("div", "", "emotion-arc");
      line.append(node("strong", valueName(cid)));
      for (const n of notes)
        line.append(
          node("span", `${n.scene_id}：${n.emotion}`, "emotion-stop"),
        );
      box.prepend(line);
    }
    void emotionHash;
  }

  function getVoices() {
    return "speechSynthesis" in window
      ? window.speechSynthesis.getVoices()
      : [];
  }
  function renderVoices() {
    const box = $("speechVoices");
    box.replaceChildren();
    if (!script) return;
    const supported =
      "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
    if (!supported) {
      $("speechStatus").textContent =
        "此浏览器不支持语音朗读，请使用支持 Web Speech 的浏览器。";
      $("speechPlay").disabled = true;
      return;
    }
    $("speechPlay").disabled = false;
    const voices = getVoices();
    $("speechStatus").textContent = voices.length
      ? `检测到 ${voices.length} 种设备声线；声线可能由浏览器本地或在线语音服务提供。`
      : "暂未枚举到声线，将尝试浏览器默认中文语音；若无法发声，可换用系统浏览器或安装中文语音。";
    for (const c of [
      { id: "narrator", name: "动作旁白" },
      ...script.characters,
    ]) {
      const stored = voiceChoices[c.id];
      const setting =
        stored && typeof stored === "object"
          ? stored
          : { voice: "", rate: 1, pitch: 1 };
      setting.rate = Math.min(2, Math.max(0.5, Number(setting.rate) || 1));
      setting.pitch = Math.min(
        2,
        Math.max(
          0,
          Number.isFinite(Number(setting.pitch)) ? Number(setting.pitch) : 1,
        ),
      );
      voiceChoices[c.id] = setting;
      const row = node("div", "", "voice-row"),
        label = node("strong", c.name),
        select = node("select");
      select.setAttribute("aria-label", `${c.name}声线`);
      select.add(new Option("默认中文声线", ""));
      for (const v of voices)
        select.add(
          new Option(
            `${v.name} · ${v.lang}${v.localService ? " · 本地" : ""}`,
            v.voiceURI,
          ),
        );
      select.value = setting.voice;
      const rate = node("input"),
        pitch = node("input");
      for (const [input, key, min, max] of [
        [rate, "rate", 0.5, 2],
        [pitch, "pitch", 0, 2],
      ]) {
        input.type = "number";
        input.min = min;
        input.max = max;
        input.step = 0.1;
        input.value = setting[key];
        input.setAttribute(
          "aria-label",
          `${c.name}${key === "rate" ? "语速" : "音高"}`,
        );
        input.addEventListener("change", () => {
          setting[key] = Math.min(max, Math.max(min, Number(input.value) || 1));
          persistVoices();
        });
      }
      select.onchange = () => {
        setting.voice = select.value;
        persistVoices();
      };
      row.append(
        label,
        select,
        node("span", "语速"),
        rate,
        node("span", "音高"),
        pitch,
      );
      box.append(row);
    }
  }
  function persistVoices() {
    try {
      localStorage.setItem(
        "story2script.studio.voices",
        JSON.stringify(voiceChoices),
      );
    } catch {
      /* optional */
    }
  }
  async function playSpeech() {
    try {
      await flushEditors();
    } catch (e) {
      status(e.message, true);
      return;
    }
    if (!script || !("speechSynthesis" in window)) return;
    stopSpeech("");
    speechSnapshot = yaml;
    const generation = speechGeneration;
    const scenes =
      $("speechScope").value === "all" ? script.scenes : [selectedScene()];
    const queue = window.StorySpeech.buildSpeechQueue(
      scenes.filter(Boolean),
      $("speechActions").checked,
    );
    let index = 0;
    function next() {
      if (generation !== speechGeneration || speechSnapshot !== yaml) return;
      if (index >= queue.length) {
        utterance = null;
        $("speechCurrent").textContent = "朗读完成。";
        return;
      }
      const line = queue[index++],
        settings = voiceChoices[line.role] || { rate: 1, pitch: 1 };
      utterance = new SpeechSynthesisUtterance(line.text);
      const voices = getVoices();
      utterance.voice =
        voices.find((v) => v.voiceURI === settings.voice) ||
        voices.find((v) => v.lang.startsWith("zh")) ||
        null;
      utterance.lang = utterance.voice?.lang || "zh-CN";
      utterance.rate = settings.rate || 1;
      utterance.pitch = settings.pitch ?? 1;
      utterance.volume = Math.min(
        1,
        Math.max(0, Number($("speechVolume").value)),
      );
      $("speechCurrent").textContent =
        `${index}/${queue.length} · ${line.scene} · ${line.role === "narrator" ? "旁白" : valueName(line.role)}：${line.text}`;
      utterance.onend = next;
      utterance.onerror = (e) => {
        if (generation !== speechGeneration) return;
        $("speechCurrent").textContent =
          `朗读中断：${e.error}。可更换声线后重试。`;
      };
      window.speechSynthesis.speak(utterance);
    }
    next();
  }

  async function exportFile(format, print = false) {
    if (!script || !yaml) return status("请先载入剧本。", true);
    if (editable() && yamlOutput.value !== workbenchYaml)
      return status("源码存在未校验编辑，请先校验后导出。", true);
    const preview = print ? window.open("about:blank", "_blank") : null;
    try {
      await flushEditors();
      const response = await fetch("/api/studio/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          yaml_text: yaml,
          format,
          include_appendix: $("exportAppendix").checked,
        }),
      });
      if (!response.ok) {
        const d = await response.json();
        throw new Error(parseError(d, "导出失败"));
      }
      const blob = await response.blob(),
        url = URL.createObjectURL(blob);
      if (print) {
        if (!preview)
          throw new Error(
            "浏览器阻止了打印预览窗口，请允许弹窗或下载 HTML 后打印。",
          );
        preview.location.href = url;
        status("打印预览已打开，可使用浏览器的打印菜单保存 PDF。");
      } else {
        const link = node("a");
        link.href = url;
        link.download = `${script.title}-${$("studioSource").value}.${format}`;
        link.click();
        status(`已导出当前查看版本的 ${format.toUpperCase()}。`);
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      preview?.close();
      status(e.message, true);
    }
  }

  window.Studio = {
    creativeBrief,
    currentYaml: () => workbenchYaml,
    onScriptChanged(next, text) {
      if (committing) return;
      workbenchYaml = text;
      const previous = yaml;
      sourceEpoch += 1;
      stopSpeech("剧本已更新，请重新开始朗读。");
      if (previous !== text) {
        draftId = "";
        draftVersion = 0;
        draftBase = text;
        savedHash = "";
        emotions = [];
        emotionYaml = "";
      }
      if (editable()) {
        script = next;
        yaml = text;
        sceneOptions();
        renderTree();
        renderEditor();
        renderVoices();
        renderEmotion();
        if (
          currentTab === "graph" &&
          !$("viewStudio").classList.contains("hidden")
        )
          refreshProjection();
      }
    },
    selectScene(id) {
      if (committing || !editable()) return;
      if (script?.scenes.some((s) => s.id === id)) $("studioScene").value = id;
    },
    setCandidate(value) {
      revision = value;
      $("studioSource")
        .querySelectorAll("option")
        .forEach((o) => (o.disabled = false));
    },
    addInlineEditor(article, id) {
      const b = node(
        "button",
        "编辑场景正文",
        "ghost-button inline-edit-toggle",
      );
      b.type = "button";
      let open = false;
      const host = node("div");
      b.onclick = () => {
        open = !open;
        if (open) {
          $("studioSource").value = "current";
          syncFromWorkbench();
          sceneEditor(
            script.scenes.find((s) => s.id === id),
            host,
          );
          b.textContent = "收起编辑";
        } else {
          host.replaceChildren();
          b.textContent = "编辑场景正文";
        }
      };
      article.append(b, host);
    },
  };
  $("viewStudioButton").onclick = () => {
    setActiveView("studio");
    try {
      if (editable()) syncFromWorkbench();
      showTab(currentTab);
    } catch (e) {
      status(e.message, true);
    }
  };
  $("studioRefresh").onclick = async () => {
    try {
      await validateYaml();
      $("studioSource").value = "current";
      syncFromWorkbench();
      refreshProjection();
    } catch (e) {
      status(e.message, true);
    }
  };
  $("studioSource").onchange = selectVersion;
  $("studioScene").onchange = async () => {
    try {
      await flushEditors();
    } catch (e) {
      status(e.message, true);
      return;
    }
    if (editable()) setCurrentScene($("studioScene").value);
    renderTree();
    renderEditor();
    if (currentTab === "graph") refreshProjection();
  };
  $("studioBoundary").onchange = refreshProjection;
  $("studioCharacter").onchange = () => {
    renderTree();
    renderEmotion();
    refreshProjection();
  };
  document
    .querySelectorAll("[data-studio-tab]")
    .forEach((b) => (b.onclick = () => showTab(b.dataset.studioTab)));
  $("structureKind").onchange = renderTree;
  $("structurePropose").onclick = async () => {
    if (!editable() || !yaml) return status("请载入当前工作稿再分组。", true);
    try {
      await flushEditors();
    } catch (error) {
      return status(error.message, true);
    }
    const sent = yaml,
      epoch = sourceEpoch,
      selectedKind = $("structureKind").value;
    try {
      const data = await aiOperation("structure", {
        yaml_text: sent,
        kind: selectedKind,
        count: Number($("structureCount").value),
        target_minutes: $("structureMinutes").value
          ? Number($("structureMinutes").value)
          : null,
        mode: $("structureMode").value,
      });
      if (yaml !== sent || yamlOutput.value !== sent || epoch !== sourceEpoch)
        throw new Error("剧本已变化，后台结果已保存，请从任务面板核对后查看。");
      renderStructureProposal(
        data.screenplay.structure.filter((g) => g.kind === selectedKind),
      );
      status("分组建议已生成，请检查标题与分界后应用。");
    } catch (e) {
      status(e.message, true);
    }
  };
  for (const [id, mode] of [
    ["studioRuleCheck", "rules"],
    ["studioAiCheck", "ai"],
  ])
    $(id).onclick = async () => {
      if (!editable() || !yaml) return;
      try {
        await flushEditors();
      } catch (e) {
        status(e.message, true);
        return;
      }
      const sent = yaml,
        epoch = sourceEpoch;
      $(id).disabled = true;
      try {
        const data = await aiOperation("recheck", { yaml_text: sent, mode });
        if (yaml !== sent || yamlOutput.value !== sent || epoch !== sourceEpoch)
          throw new Error("核验期间剧本发生变化，结果未覆盖当前编辑。");
        if (mode === "ai") {
          accept(data);
          await saveDraft();
          jobApplied = true;
        }
        const box = $("studioChecks");
        box.replaceChildren(node("p", data.message));
        for (const issue of [
          ...(data.issues || []),
          ...(data.reasoning?.issues || []),
        ])
          box.append(
            node("p", `${issue.scene_id}：${issue.message}`, "plot-error"),
          );
        status(data.message, !data.valid);
      } catch (e) {
        status(e.message, true);
      } finally {
        $(id).disabled = false;
        jobButtons();
      }
    };
  $("studioSaveDraft").onclick = () =>
    flushEditors()
      .then(() => saveDraft())
      .then(() => status("草稿已保存。"))
      .catch((e) => status(e.message, true));
  $("studioNewDraft").onclick = () =>
    flushEditors()
      .then(() => saveDraft(true))
      .then(() => status("已另存为独立草稿。"))
      .catch((e) => status(e.message, true));
  $("studioListDrafts").onclick = listDrafts;
  $("creativeTemplate").onchange = () => {
    const b = templateList.find(
      (v) =>
        `${v.template_id}:${v.template_version}` ===
        $("creativeTemplate").value,
    );
    templateEnabled = !!b;
    if (!b) return;
    $("creativeName").value = b.name;
    $("creativeTone").value = b.tone;
    $("creativePacing").value = b.pacing;
    $("creativeFidelity").value = b.fidelity;
    $("creativeInstructions").value = b.instructions;
  };
  for (const id of [
    "creativeName",
    "creativeTone",
    "creativePacing",
    "creativeFidelity",
    "creativeInstructions",
  ])
    $(id).addEventListener("input", () => {
      templateEnabled = true;
    });
  $("saveCreativeTemplate").onclick = async () => {
    try {
      templateEnabled = true;
      const b = await api("templates", creativeBrief());
      await loadTemplates();
      $("creativeTemplate").value = `${b.template_id}:${b.template_version}`;
      $("creativeStatus").textContent =
        `已保存 ${b.name} v${b.template_version}；旧剧本继续保留原模板快照。`;
    } catch (e) {
      $("creativeStatus").textContent = e.message;
    }
  };
  $("applyCreativeBrief").onclick = async () => {
    try {
      $("studioSource").value = "current";
      syncFromWorkbench();
      templateEnabled = true;
      accept(await safeEdit({ creative_brief: creativeBrief() }));
      await saveDraft();
      $("creativeStatus").textContent =
        "偏好已保存，后续 AI 润色与剧情改写会使用；现有正文未自动重写。";
    } catch (e) {
      $("creativeStatus").textContent = e.message;
    }
  };
  $("emotionAnnotated").onclick = () => readEmotions("annotated");
  $("emotionAI").onclick = () => readEmotions("ai");
  $("speechPlay").onclick = playSpeech;
  $("speechPause").onclick = () => {
    window.speechSynthesis?.pause();
    if (utterance)
      $("speechCurrent").textContent =
        "已暂停 · " + $("speechCurrent").textContent.replace(/^已暂停 · /, "");
  };
  $("speechResume").onclick = () => {
    window.speechSynthesis?.resume();
    $("speechCurrent").textContent = $("speechCurrent").textContent.replace(
      /^已暂停 · /,
      "",
    );
  };
  $("speechStop").onclick = () => stopSpeech();
  if ("speechSynthesis" in window)
    window.speechSynthesis.addEventListener("voiceschanged", renderVoices);
  try {
    voiceChoices = JSON.parse(
      localStorage.getItem("story2script.studio.voices") || "{}",
    );
    if (
      !voiceChoices ||
      Array.isArray(voiceChoices) ||
      typeof voiceChoices !== "object"
    )
      voiceChoices = {};
  } catch {
    voiceChoices = {};
  }
  document
    .querySelectorAll("[data-studio-export]")
    .forEach((b) => (b.onclick = () => exportFile(b.dataset.studioExport)));
  $("studioPrint").onclick = () => exportFile("html", true);
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      Promise.all(
        [...queuedSaves]
          .filter((s) => s.form?.isConnected)
          .map((s) => s.flush()),
      )
        .then(() => saveDraft())
        .catch((err) => status(err.message, true));
    }
  });
  window.addEventListener("beforeunload", (e) => {
    window.speechSynthesis?.cancel();
    if ([...queuedSaves].some((s) => s.form?.isConnected && s.dirty())) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  loadTemplates();
  void savedHash;
})();
