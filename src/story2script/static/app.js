const titleInput = document.querySelector("#titleInput");
const genreInput = document.querySelector("#genreInput");
const adaptationTypeInput = document.querySelector("#adaptationTypeInput");
const convertModeInput = document.querySelector("#convertModeInput");
const novelInput = document.querySelector("#novelInput");
const chapterView = document.querySelector("#chapterView");
const novelEditViewButton = document.querySelector("#novelEditViewButton");
const novelChapterViewButton = document.querySelector("#novelChapterViewButton");
const yamlOutput = document.querySelector("#yamlOutput");
const scriptPreview = document.querySelector("#scriptPreview");
const previewViewButton = document.querySelector("#previewViewButton");
const sourceViewButton = document.querySelector("#sourceViewButton");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const conversionProgress = document.querySelector("#conversionProgress");
const conversionProgressStage = document.querySelector("#conversionProgressStage");
const conversionProgressPercent = document.querySelector("#conversionProgressPercent");
const conversionProgressBar = document.querySelector("#conversionProgressBar");
const conversionProgressMessage = document.querySelector("#conversionProgressMessage");

let currentScriptView = "preview";
let currentNovelView = "edit";
let latestScreenplay = null;
let latestReviewReport = null;
const conversionPollIntervalMs = 1000;
const conversionMaxPolls = 720;
const convertButton = document.querySelector("#convertButton");
const analyzeCharactersButton = document.querySelector("#analyzeCharactersButton");
const importNovelFileButton = document.querySelector("#importNovelFileButton");
const novelFileInput = document.querySelector("#novelFileInput");
const sceneIdInput = document.querySelector("#sceneIdInput");
const rewriteModeInput = document.querySelector("#rewriteModeInput");
const rewriteCharacterInput = document.querySelector("#rewriteCharacterInput");
const rewriteToneInput = document.querySelector("#rewriteToneInput");
const rewriteButtons = document.querySelectorAll("[data-rewrite-operation]");
const enableReviewInput = document.querySelector("#enableReviewInput");
const reviewModeInput = document.querySelector("#reviewModeInput");
const reviewThresholdInput = document.querySelector("#reviewThresholdInput");
const reviewAutoFixInput = document.querySelector("#reviewAutoFixInput");
const runReviewButton = document.querySelector("#runReviewButton");
const downloadReviewReportButton = document.querySelector("#downloadReviewReportButton");
const profileGrid = document.querySelector("#profileGrid");
const profileEmptyState = document.querySelector("#profileEmptyState");
const agentGoalInput = document.querySelector("#agentGoalInput");
const agentModeInput = document.querySelector("#agentModeInput");
const agentThresholdInput = document.querySelector("#agentThresholdInput");
const agentMaxStepsInput = document.querySelector("#agentMaxStepsInput");
const agentSaveSessionInput = document.querySelector("#agentSaveSessionInput");
const runAgentButton = document.querySelector("#runAgentButton");
const listAgentSessionsButton = document.querySelector("#listAgentSessionsButton");
const agentProgress = document.querySelector("#agentProgress");
const agentProgressStage = document.querySelector("#agentProgressStage");
const agentProgressPercent = document.querySelector("#agentProgressPercent");
const agentProgressBar = document.querySelector("#agentProgressBar");
const agentProgressMessage = document.querySelector("#agentProgressMessage");
const agentEmptyState = document.querySelector("#agentEmptyState");
const agentSummary = document.querySelector("#agentSummary");
const agentTrace = document.querySelector("#agentTrace");
const agentSessions = document.querySelector("#agentSessions");
const refreshMetricsButton = document.querySelector("#refreshMetricsButton");
const metricsEmptyState = document.querySelector("#metricsEmptyState");
const metricsContent = document.querySelector("#metricsContent");
const agentPollIntervalMs = 1000;
const agentMaxPolls = 720;
const supportedTextNovelFileExtensions = [".txt", ".text", ".md", ".markdown", ".csv", ".log"];
const unsupportedEbookFileExtensions = [".mobi", ".azw", ".azw3"];

function setMessage(text, isError = false) {
  message.textContent = text;
  message.style.color = isError ? "#c33b26" : "";
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function setConversionProgress(snapshot, isError = false) {
  const progress = Math.max(0, Math.min(100, snapshot.progress || 0));
  conversionProgress.classList.remove("hidden");
  conversionProgress.classList.toggle("is-error", isError || snapshot.status === "failed");
  conversionProgressStage.textContent = snapshot.stage || "转换中";
  conversionProgressPercent.textContent = `${progress}%`;
  conversionProgressBar.style.width = `${progress}%`;
  conversionProgressMessage.textContent = snapshot.message || snapshot.error || "转换任务正在处理。";
}

function resetConversionProgress() {
  setConversionProgress({
    status: "queued",
    progress: 0,
    stage: "等待转换",
    message: "转换任务已准备启动。",
  });
}

// 与后端 parser.py 一致：只把“标题后到下一个标题之间有正文”的才算章节，
// 这样目录里的“第一章/第二章……”这类空标题不会被误计成章节。
const CHAPTER_HEADING_RE =
  /^[ \t]*(第[零一二两三四五六七八九十百千万\d]+章[^\r\n]*|chapter\s+\d+[^\r\n]*)[ \t]*$/gim;

function detectChapters(text) {
  const matches = [...text.matchAll(CHAPTER_HEADING_RE)];
  const chapters = [];
  for (let index = 0; index < matches.length; index += 1) {
    const start = matches[index].index + matches[index][0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
    const content = text.slice(start, end).trim();
    if (content) {
      chapters.push({ title: matches[index][1].trim(), content });
    }
  }
  return chapters;
}

function updateChapterCount() {
  const chapters = detectChapters(novelInput.value);
  document.querySelector("#chapterCount").textContent = `已识别 ${chapters.length} 个章节`;
  if (currentNovelView === "chapter") {
    renderChapterView();
  }
}

function renderChapterView() {
  chapterView.innerHTML = "";
  const chapters = detectChapters(novelInput.value);
  if (chapters.length === 0) {
    chapterView.appendChild(
      createElement("p", "chapter-empty", "未识别到章节，请使用“第一章 标题”格式标记。"),
    );
    return;
  }
  chapters.forEach((chapter, index) => {
    const item = createElement("details", "chapter-item");
    if (index === 0) {
      item.open = true;
    }
    item.appendChild(
      createElement("summary", "chapter-title", `${chapter.title}（${chapter.content.length} 字）`),
    );
    item.appendChild(createElement("div", "chapter-text", chapter.content));
    chapterView.appendChild(item);
  });
}

function setNovelView(view) {
  currentNovelView = view;
  novelEditViewButton.classList.toggle("is-active", view === "edit");
  novelChapterViewButton.classList.toggle("is-active", view === "chapter");
  novelInput.classList.toggle("hidden", view !== "edit");
  chapterView.classList.toggle("hidden", view !== "chapter");
  if (view === "chapter") {
    renderChapterView();
  }
}

function errorMessage(data, fallback) {
  return typeof data.detail === "string" ? data.detail : fallback;
}

function fileExtension(fileName) {
  const dotIndex = fileName.lastIndexOf(".");
  return dotIndex >= 0 ? fileName.slice(dotIndex).toLowerCase() : "";
}

function fileTitle(fileName) {
  const dotIndex = fileName.lastIndexOf(".");
  return dotIndex > 0 ? fileName.slice(0, dotIndex) : fileName;
}

function isSupportedTextNovelFile(file) {
  if (file.type.startsWith("text/")) {
    return true;
  }
  return supportedTextNovelFileExtensions.includes(fileExtension(file.name));
}

function isEpubNovelFile(file) {
  return fileExtension(file.name) === ".epub";
}

function isUnsupportedEbookFile(file) {
  return unsupportedEbookFileExtensions.includes(fileExtension(file.name));
}

function decodeNovelFileContent(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return new TextDecoder("gb18030").decode(buffer);
  }
}

function readFileAsArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (reader.result instanceof ArrayBuffer) {
        resolve(reader.result);
        return;
      }
      reject(new Error("文件读取失败"));
    });
    reader.addEventListener("error", () => {
      reject(new Error("文件读取失败，请确认文件未被占用或损坏。"));
    });
    reader.readAsArrayBuffer(file);
  });
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

function createProfileRow(label, value) {
  const row = document.createElement("tr");
  const heading = document.createElement("th");
  const cell = document.createElement("td");

  heading.textContent = label;
  cell.textContent = value;
  row.append(heading, cell);

  return row;
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null && text !== "") {
    node.textContent = text;
  }
  return node;
}

function characterNameMap(screenplay) {
  const map = new Map();
  (screenplay.characters || []).forEach((character) => {
    map.set(character.id, character.name || character.id);
  });
  return map;
}

function dialogueCue(element, names) {
  const speaker = names.get(element.character) || element.character || "未署名";
  const notes = [element.parenthetical, element.emotion].filter((item) => item && item.trim());
  return notes.length ? `${speaker}（${notes.join(" · ")}）` : speaker;
}

function renderSceneMeta(scene) {
  const meta = createElement("div", "scene-beats");
  [
    ["目标", scene.goal],
    ["冲突", scene.conflict],
    ["节拍", scene.beat],
    ["潜台词", scene.subtext],
  ].forEach(([label, value]) => {
    if (!value) {
      return;
    }
    const row = createElement("div", "scene-beat");
    row.append(createElement("span", "beat-label", label), createElement("span", "beat-text", value));
    meta.appendChild(row);
  });
  return meta;
}

function renderChips(label, values) {
  if (!values || values.length === 0) {
    return null;
  }
  const wrap = createElement("div", "scene-chips");
  wrap.appendChild(createElement("span", "chips-label", label));
  values.forEach((value) => wrap.appendChild(createElement("span", "chip", value)));
  return wrap;
}

function renderSceneBody(scene, names) {
  const body = createElement("div", "scene-body");
  (scene.elements || []).forEach((element) => {
    if (element.type === "dialogue") {
      const block = createElement("div", "line-dialogue");
      block.append(
        createElement("span", "cue", dialogueCue(element, names)),
        createElement("p", "dlg", element.text),
      );
      body.appendChild(block);
      return;
    }
    body.appendChild(createElement("p", "line-action", element.text));
  });
  return body;
}

function renderDecisions(scene) {
  const decisions = scene.dramatization_decisions || [];
  if (decisions.length === 0) {
    return null;
  }
  const details = createElement("details", "scene-decisions");
  details.appendChild(createElement("summary", null, `叙述 → 戏剧化决策（${decisions.length}）`));
  decisions.forEach((decision) => {
    const row = createElement("div", "decision-row");
    row.appendChild(createElement("span", `decision-target target-${decision.target}`, decision.target));
    const detail = createElement("div", "decision-detail");
    detail.appendChild(createElement("p", "decision-source", decision.source_text));
    if (decision.rendering && decision.rendering !== decision.source_text) {
      detail.appendChild(createElement("p", "decision-rendering", `→ ${decision.rendering}`));
    }
    // 只展示“原文 → 改写方向”的分类分析，不再展示方法论式的 reason 说明文字。
    row.appendChild(detail);
    details.appendChild(row);
  });
  return details;
}

const CRITERIA_LABELS = {
  dramatization: "戏剧化",
  dialogue_conflict: "对白冲突",
  residual_narration: "残留旁白",
  character_voice: "人物语气",
};

function emptyReviewReport() {
  return {
    version: "1",
    screenplay_title: latestScreenplay ? latestScreenplay.title : "",
    mode: reviewModeInput.value,
    threshold: Number(reviewThresholdInput.value) || 7,
    rounds_used: 0,
    machine: {},
    human: {},
    summary: {},
  };
}

function ensureReviewReport() {
  if (!latestReviewReport) {
    latestReviewReport = emptyReviewReport();
  }
  return latestReviewReport;
}

function renderReviewBadge(sceneId) {
  const result = latestReviewReport && latestReviewReport.machine[sceneId];
  if (!result) {
    return null;
  }
  const passed = result.verdict === "pass";
  const badge = createElement(
    "span",
    `review-badge ${passed ? "review-pass" : "review-fail"}`,
    `机审 ${result.total} 分 · ${passed ? "通过" : "未通过"}`,
  );
  const details = Object.entries(result.scores || {})
    .map(([key, value]) => `${CRITERIA_LABELS[key] || key}：${value}`)
    .join("\n");
  const issues = (result.issues || []).join("\n");
  badge.title = issues ? `${details}\n问题：\n${issues}` : details;
  return badge;
}

function renderHumanReviewControls(sceneId) {
  const wrap = createElement("div", "human-review");
  const verdict = (latestReviewReport && latestReviewReport.human[sceneId]) || null;

  const label = createElement("span", "human-review-label", "人审");
  const approveButton = createElement(
    "button",
    `ghost-button review-approve${verdict && verdict.status === "approved" ? " is-active" : ""}`,
    verdict && verdict.status === "approved" ? "已通过" : "通过",
  );
  approveButton.type = "button";
  const rejectButton = createElement(
    "button",
    `ghost-button review-reject${verdict && verdict.status === "rejected" ? " is-active" : ""}`,
    verdict && verdict.status === "rejected" ? "已驳回" : "驳回",
  );
  rejectButton.type = "button";
  const commentInput = document.createElement("input");
  commentInput.className = "human-review-comment";
  commentInput.placeholder = "审阅意见（可选）";
  commentInput.value = verdict ? verdict.comment : "";

  const record = (status) => {
    const report = ensureReviewReport();
    report.human[sceneId] = {
      scene_id: sceneId,
      status,
      comment: commentInput.value.trim(),
      reviewed_at: new Date().toISOString(),
    };
    renderScreenplay(latestScreenplay);
    setMessage(`已记录 ${sceneId} 的人审结论：${status === "approved" ? "通过" : "驳回"}。`);
  };
  approveButton.addEventListener("click", () => record("approved"));
  rejectButton.addEventListener("click", () => record("rejected"));
  commentInput.addEventListener("change", () => {
    if (latestReviewReport && latestReviewReport.human[sceneId]) {
      latestReviewReport.human[sceneId].comment = commentInput.value.trim();
    }
  });

  wrap.append(label, approveButton, rejectButton, commentInput);
  return wrap;
}

async function runMachineReview() {
  if (!yamlOutput.value) {
    setMessage("请先生成或输入 YAML 剧本。", true);
    return;
  }

  runReviewButton.disabled = true;
  const modeName = reviewModeInput.value === "ai" ? "AI" : "本地";
  setMessage(`正在使用${modeName}模式机审场景……`);

  try {
    const response = await fetch("/api/scenes/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        yaml_text: yamlOutput.value,
        mode: reviewModeInput.value,
        auto_fix: reviewAutoFixInput.checked,
        threshold: Number(reviewThresholdInput.value) || null,
      }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(errorMessage(data, "机审失败"));
    }

    const previousHuman = latestReviewReport ? latestReviewReport.human : {};
    latestReviewReport = data.report;
    latestReviewReport.human = { ...previousHuman, ...latestReviewReport.human };
    if (data.screenplay && data.yaml_text) {
      showScreenplay(data.screenplay, data.yaml_text);
    } else {
      renderScreenplay(latestScreenplay);
    }
    setMessage(data.message);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    runReviewButton.disabled = false;
  }
}

function downloadReviewReport() {
  if (!latestReviewReport) {
    setMessage("请先执行机审或记录人审结论。", true);
    return;
  }

  const blob = new Blob([JSON.stringify(latestReviewReport, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "review-report.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderScene(scene, index, names) {
  const article = createElement("article", "scene");
  article.dataset.sceneId = scene.id;

  const slug = createElement("div", "scene-slug");
  const number = createElement("button", "scene-no", `SCENE ${index + 1}`);
  number.type = "button";
  number.title = "点击把此场景填入重写工具";
  number.addEventListener("click", () => {
    sceneIdInput.value = scene.id;
    setMessage(`已选择 ${scene.id} 进行局部重写。`);
  });
  slug.append(
    number,
    createElement("span", "slug-line", scene.heading),
    createElement("span", "scene-source", scene.source_chapter),
  );
  const reviewBadge = renderReviewBadge(scene.id);
  if (reviewBadge) {
    slug.appendChild(reviewBadge);
  }
  article.appendChild(slug);

  if (scene.summary) {
    article.appendChild(createElement("p", "scene-summary", scene.summary));
  }
  article.appendChild(renderSceneMeta(scene));

  const presentNames = (scene.characters_present || []).map((id) => names.get(id) || id);
  const present = renderChips("出场", presentNames);
  if (present) {
    article.appendChild(present);
  }
  const props = renderChips("道具", scene.props || []);
  if (props) {
    article.appendChild(props);
  }

  article.appendChild(renderSceneBody(scene, names));

  if (scene.camera_hints && scene.camera_hints.length) {
    const camera = createElement("div", "scene-camera");
    camera.appendChild(createElement("span", "camera-label", "镜头"));
    scene.camera_hints.forEach((hint) => camera.appendChild(createElement("p", "camera-hint", hint)));
    article.appendChild(camera);
  }

  const decisions = renderDecisions(scene);
  if (decisions) {
    article.appendChild(decisions);
  }
  article.appendChild(renderHumanReviewControls(scene.id));
  return article;
}

function renderScreenplay(screenplay) {
  scriptPreview.innerHTML = "";
  if (!screenplay) {
    return;
  }
  const names = characterNameMap(screenplay);

  const header = createElement("header", "script-meta");
  header.appendChild(createElement("h3", "script-title", screenplay.title));
  if (screenplay.logline) {
    header.appendChild(createElement("p", "script-logline", screenplay.logline));
  }
  const tags = createElement("div", "script-tags");
  if (screenplay.genre) {
    tags.appendChild(createElement("span", "tag", screenplay.genre));
  }
  if (screenplay.adaptation_type) {
    tags.appendChild(createElement("span", "tag tag-accent", screenplay.adaptation_type));
  }
  tags.appendChild(createElement("span", "tag", `${(screenplay.scenes || []).length} 场`));
  header.appendChild(tags);
  scriptPreview.appendChild(header);

  (screenplay.scenes || []).forEach((scene, index) => {
    scriptPreview.appendChild(renderScene(scene, index, names));
  });
}

function setScriptView(view) {
  currentScriptView = view;
  previewViewButton.classList.toggle("is-active", view === "preview");
  sourceViewButton.classList.toggle("is-active", view === "source");

  if (!yamlOutput.value) {
    emptyState.classList.remove("hidden");
    scriptPreview.classList.add("hidden");
    yamlOutput.classList.add("hidden");
    return;
  }

  emptyState.classList.add("hidden");
  scriptPreview.classList.toggle("hidden", view !== "preview");
  yamlOutput.classList.toggle("hidden", view !== "source");
}

function populateCharacterOptions(screenplay) {
  // 重写工具按角色“名字”选择，内部仍提交稳定的 character id，用户无需知道 id。
  const previous = rewriteCharacterInput.value;
  rewriteCharacterInput.innerHTML = "";
  rewriteCharacterInput.appendChild(createElement("option", null, "（自动选择）"));
  (screenplay.characters || []).forEach((character) => {
    const option = createElement("option", null, character.name || character.id);
    option.value = character.id;
    rewriteCharacterInput.appendChild(option);
  });
  const stillExists = (screenplay.characters || []).some((character) => character.id === previous);
  rewriteCharacterInput.value = stillExists ? previous : "";
}

function showScreenplay(screenplay, yamlText) {
  latestScreenplay = screenplay;
  yamlOutput.value = yamlText;
  renderScreenplay(screenplay);
  populateCharacterOptions(screenplay);
  setScriptView(currentScriptView);
}

function renderProfiles(profiles) {
  profileGrid.innerHTML = "";
  profileEmptyState.classList.toggle("hidden", profiles.length > 0);

  profiles.forEach((profile) => {
    const card = document.createElement("article");
    const name = document.createElement("h3");
    const table = document.createElement("table");

    card.className = "profile-card";
    name.textContent = profile.name;
    table.append(
      createProfileRow("角色定位", profile.role),
      createProfileRow("性格", profile.personality),
      createProfileRow("目标", profile.goal),
      createProfileRow("与他人的关系", profile.relationships.join("；")),
      createProfileRow("出场章节", profile.appearance_chapters.join("、")),
      createProfileRow("关键变化", profile.key_change),
    );
    card.append(name, table);
    profileGrid.appendChild(card);
  });
}

async function loadExample() {
  setMessage("正在加载示例小说……");
  const response = await fetch("/api/examples/novel");
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "示例加载失败");
  }

  titleInput.value = data.title;
  genreInput.value = data.genre;
  adaptationTypeInput.value = "影视剧";
  convertModeInput.value = "demo";
  novelInput.value = data.novel_text;
  updateChapterCount();
  setMessage("示例小说已填入。");
}

function applyImportedNovel(fileName, title, content) {
  novelInput.value = content;
  if (!titleInput.value.trim()) {
    titleInput.value = title || fileTitle(fileName);
  }
  updateChapterCount();
}

async function importTextNovelFile(file) {
  const buffer = await readFileAsArrayBuffer(file);
  applyImportedNovel(file.name, fileTitle(file.name), decodeNovelFileContent(buffer));
  setMessage(`已导入 ${file.name}。`);
}

async function importEpubNovelFile(file) {
  setMessage("正在解析 EPUB 文件……");
  const buffer = await readFileAsArrayBuffer(file);
  const response = await fetch("/api/novels/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_name: file.name,
      content_base64: arrayBufferToBase64(buffer),
    }),
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(errorMessage(data, "EPUB 解析失败"));
  }

  applyImportedNovel(file.name, data.title, data.novel_text);
  setMessage(`已导入 ${data.file_name}，解析出 ${data.character_count} 个字符。`);
}

function importNovelFile(file) {
  if (!file) {
    return;
  }
  if (isUnsupportedEbookFile(file)) {
    setMessage("暂不支持直接解析 MOBI/AZW/AZW3，请先转换为 EPUB 或 TXT 后导入。", true);
    return;
  }
  if (!isSupportedTextNovelFile(file) && !isEpubNovelFile(file)) {
    setMessage("暂支持导入 EPUB、TXT、Markdown、CSV 和 LOG 等小说文件。", true);
    return;
  }

  const importTask = isEpubNovelFile(file) ? importEpubNovelFile(file) : importTextNovelFile(file);
  importTask.catch((error) => {
    setMessage(error.message, true);
  });
}

function conversionPayload() {
  return {
    title: titleInput.value,
    genre: genreInput.value,
    adaptation_type: adaptationTypeInput.value,
    mode: convertModeInput.value,
    novel_text: novelInput.value,
    enable_review: enableReviewInput.checked,
  };
}

async function startConversionJob() {
  const response = await fetch("/api/convert/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(conversionPayload()),
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(errorMessage(data, "转换任务创建失败"));
  }
  setConversionProgress(data);
  return data.job_id;
}

async function fetchConversionJob(jobId) {
  const response = await fetch(`/api/convert/jobs/${jobId}`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(errorMessage(data, "转换进度查询失败"));
  }
  setConversionProgress(data);
  return data;
}

async function waitForConversionJob(jobId) {
  for (let attempt = 0; attempt < conversionMaxPolls; attempt += 1) {
    await sleep(conversionPollIntervalMs);
    const snapshot = await fetchConversionJob(jobId);
    if (snapshot.status === "succeeded" || snapshot.status === "failed") {
      return snapshot;
    }
  }

  throw new Error("转换任务等待超时，请稍后重试。");
}

async function convertNovel() {
  convertButton.disabled = true;
  const modeName = convertModeInput.value === "ai" ? "AI" : "本地";
  setMessage(`正在使用${modeName}模式生成 YAML 剧本……`);
  resetConversionProgress();
  // 重新转换会重排 scene id，旧的审校报告随之失效。
  latestReviewReport = null;

  try {
    const jobId = await startConversionJob();
    const snapshot = await waitForConversionJob(jobId);

    if (snapshot.status === "failed") {
      setConversionProgress(snapshot, true);
      throw new Error(snapshot.error || snapshot.message || "转换失败");
    }
    if (!snapshot.result) {
      throw new Error("转换任务完成但缺少结果。");
    }

    const data = snapshot.result;
    if (data.review_report) {
      latestReviewReport = data.review_report;
    }
    showScreenplay(data.screenplay, data.yaml_text);
    const securityWarnings = data.security_warnings || [];
    setMessage(
      `已生成 ${data.screenplay.scenes.length} 个场景，改编类型：${data.adaptation_type}，当前模式：${data.mode}。` +
        (data.review_report
          ? `机审：${data.review_report.summary.pass_count || 0} 个通过，${
              data.review_report.summary.fail_count || 0
            } 个未通过。`
          : "") +
        (securityWarnings.length
          ? `安全提示：原文含 ${securityWarnings.length} 处疑似提示注入内容（已按数据处理，未影响转换）。`
          : ""),
    );
  } catch (error) {
    setMessage(error.message, true);
    conversionProgress.classList.add("is-error");
  } finally {
    convertButton.disabled = false;
  }
}

async function analyzeCharacters() {
  analyzeCharactersButton.disabled = true;
  const modeName = convertModeInput.value === "ai" ? "AI" : "本地";
  setMessage(`正在使用${modeName}模式分析人物小传……`);

  try {
    const response = await fetch("/api/characters/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ novel_text: novelInput.value, mode: convertModeInput.value }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "人物分析失败");
    }

    renderProfiles(data.profiles);
    setMessage(`已提取 ${data.profiles.length} 个人物小传，当前模式：${data.mode}。`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    analyzeCharactersButton.disabled = false;
  }
}

async function validateYaml() {
  if (!yamlOutput.value) {
    setMessage("请先生成或输入 YAML 剧本。", true);
    return;
  }

  const response = await fetch("/api/yaml/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml_text: yamlOutput.value }),
  });
  const data = await response.json();

  if (!response.ok) {
    setMessage(data.detail || "YAML 校验失败", true);
    return;
  }

  if (data.screenplay) {
    latestScreenplay = data.screenplay;
    renderScreenplay(data.screenplay);
    setScriptView(currentScriptView);
  }
  setMessage(data.message);
}

async function rewriteScene(operation) {
  if (!yamlOutput.value) {
    setMessage("请先生成或输入 YAML 剧本。", true);
    return;
  }

  rewriteButtons.forEach((button) => {
    button.disabled = true;
  });
  setMessage("正在局部重写场景……");

  try {
    const response = await fetch("/api/scenes/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        yaml_text: yamlOutput.value,
        scene_id: sceneIdInput.value,
        operation,
        mode: rewriteModeInput.value,
        character_id: rewriteCharacterInput.value,
        tone: rewriteToneInput.value,
      }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(errorMessage(data, "局部重写失败"));
    }

    showScreenplay(data.screenplay, data.yaml_text);
    setMessage(`${data.message} 已更新 ${data.scene_id}，模式：${data.mode}。`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    rewriteButtons.forEach((button) => {
      button.disabled = false;
    });
  }
}

function downloadYaml() {
  if (!yamlOutput.value) {
    setMessage("请先生成 YAML 剧本。", true);
    return;
  }

  const blob = new Blob([yamlOutput.value], { type: "text/yaml;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "screenplay.yaml";
  link.click();
  URL.revokeObjectURL(link.href);
}

const AGENT_STATUS_LABELS = {
  completed: "已完成",
  budget_exhausted: "步数耗尽",
  failed: "已失败",
};

function setAgentProgress(snapshot, isError = false) {
  const progress = Math.max(0, Math.min(100, snapshot.progress || 0));
  agentProgress.classList.remove("hidden");
  agentProgress.classList.toggle("is-error", isError || snapshot.status === "failed");
  agentProgressStage.textContent = snapshot.stage || "执行中";
  agentProgressPercent.textContent = `${progress}%`;
  agentProgressBar.style.width = `${progress}%`;
  agentProgressMessage.textContent = snapshot.message || snapshot.error || "Agent 任务正在处理。";
}

function resetAgentProgress() {
  setAgentProgress({
    status: "queued",
    progress: 0,
    stage: "等待执行",
    message: "Agent 任务已准备启动。",
  });
}

function agentMetric(label, value) {
  const chip = createElement("span", "agent-metric");
  chip.append(
    createElement("span", "agent-metric-label", label),
    createElement("strong", "agent-metric-value", `${value}`),
  );
  return chip;
}

function renderAgentSummary(result) {
  agentEmptyState.classList.add("hidden");
  agentSummary.classList.remove("hidden");
  agentSummary.innerHTML = "";

  const statusBadge = createElement(
    "span",
    `agent-status agent-status-${result.status}`,
    AGENT_STATUS_LABELS[result.status] || result.status,
  );
  const heading = createElement("div", "agent-summary-heading");
  heading.append(statusBadge, createElement("span", "agent-goal-text", result.goal || "（默认目标：全部场景通过机审）"));
  agentSummary.appendChild(heading);

  const initial = result.initial_summary || {};
  const final = result.final_summary || {};
  const metrics = createElement("div", "agent-metrics");
  metrics.append(
    agentMetric("均分", `${initial.avg_score ?? "-"} → ${final.avg_score ?? "-"}`),
    agentMetric("通过场景", `${initial.pass_count ?? "-"} → ${final.pass_count ?? "-"}`),
    agentMetric("步数", result.steps_used),
    agentMetric("LLM 调用", result.llm_calls),
  );
  if (result.session_id) {
    metrics.append(agentMetric("会话", result.session_id));
  }
  agentSummary.appendChild(metrics);
  if (result.message) {
    agentSummary.appendChild(createElement("p", "agent-summary-message", result.message));
  }
}

function renderAgentTrace(trace) {
  agentTrace.classList.remove("hidden");
  agentTrace.innerHTML = "";
  (trace || []).forEach((step) => {
    const item = createElement("li", `agent-step${step.error ? " agent-step-error" : ""}`);
    const head = createElement("div", "agent-step-head");
    head.append(
      createElement("span", "agent-step-no", `第 ${step.step} 步`),
      createElement(
        "span",
        "agent-step-action",
        step.action ? step.action.tool : "（无动作）",
      ),
      createElement("span", "agent-step-duration", `${step.duration_ms} ms`),
    );
    item.appendChild(head);
    if (step.thought) {
      item.appendChild(createElement("p", "agent-step-thought", step.thought));
    }
    if (step.action && Object.keys(step.action.params || {}).length) {
      item.appendChild(
        createElement("code", "agent-step-params", JSON.stringify(step.action.params)),
      );
    }
    if (step.error) {
      item.appendChild(createElement("p", "agent-step-error-text", step.error));
    } else if (step.observation && step.observation.message) {
      item.appendChild(createElement("p", "agent-step-observation", step.observation.message));
    }
    agentTrace.appendChild(item);
  });
}

function applyAgentRunResponse(data) {
  const previousHuman = latestReviewReport ? latestReviewReport.human : {};
  if (data.report) {
    latestReviewReport = data.report;
    latestReviewReport.human = { ...previousHuman, ...latestReviewReport.human };
  }
  showScreenplay(data.screenplay, data.yaml_text);
  renderAgentSummary(data.result);
  renderAgentTrace(data.result.trace);
}

function agentRunPayload() {
  return {
    yaml_text: yamlOutput.value,
    goal: agentGoalInput.value.trim(),
    mode: agentModeInput.value,
    threshold: Number(agentThresholdInput.value) || null,
    max_steps: Number(agentMaxStepsInput.value) || null,
    save_session: agentSaveSessionInput.checked,
  };
}

async function startAgentRun() {
  const response = await fetch("/api/agent/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(agentRunPayload()),
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(errorMessage(data, "Agent 任务创建失败"));
  }
  setAgentProgress(data);
  return data.job_id;
}

async function fetchAgentRun(jobId) {
  const response = await fetch(`/api/agent/runs/${jobId}`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(errorMessage(data, "Agent 进度查询失败"));
  }
  setAgentProgress(data);
  return data;
}

async function waitForAgentRun(jobId) {
  for (let attempt = 0; attempt < agentMaxPolls; attempt += 1) {
    await sleep(agentPollIntervalMs);
    const snapshot = await fetchAgentRun(jobId);
    if (snapshot.status === "succeeded" || snapshot.status === "failed") {
      return snapshot;
    }
  }

  throw new Error("Agent 任务等待超时，请稍后重试。");
}

async function runAgent() {
  if (!yamlOutput.value) {
    setMessage("请先生成或输入 YAML 剧本，再启动改编 Agent。", true);
    return;
  }

  runAgentButton.disabled = true;
  agentSessions.classList.add("hidden");
  const modeName = agentModeInput.value === "ai" ? "AI" : "本地";
  setMessage(`改编 Agent（${modeName}模式）已启动，自主执行审校与重写……`);
  resetAgentProgress();

  try {
    const jobId = await startAgentRun();
    const snapshot = await waitForAgentRun(jobId);

    if (snapshot.status === "failed") {
      setAgentProgress(snapshot, true);
      throw new Error(snapshot.error || snapshot.message || "Agent 执行失败");
    }
    if (!snapshot.result) {
      throw new Error("Agent 任务完成但缺少结果。");
    }

    applyAgentRunResponse(snapshot.result);
    const result = snapshot.result.result;
    setMessage(
      `Agent ${AGENT_STATUS_LABELS[result.status] || result.status}：${result.message} ` +
        `均分 ${result.initial_summary.avg_score ?? "-"} → ${result.final_summary.avg_score ?? "-"}，` +
        `共 ${result.steps_used} 步。`,
    );
  } catch (error) {
    setMessage(error.message, true);
    agentProgress.classList.add("is-error");
  } finally {
    runAgentButton.disabled = false;
  }
}

async function loadAgentSession(sessionId) {
  setMessage(`正在载入会话 ${sessionId}……`);
  const response = await fetch(`/api/agent/sessions/${sessionId}`);
  const data = await response.json();

  if (!response.ok) {
    setMessage(errorMessage(data, "会话载入失败"), true);
    return;
  }

  applyAgentRunResponse(data);
  setMessage(`已恢复会话 ${sessionId}：${data.goal || "（无目标描述）"}。`);
}

async function listAgentSessions() {
  listAgentSessionsButton.disabled = true;
  try {
    const response = await fetch("/api/agent/sessions");
    const data = await response.json();

    if (!response.ok) {
      throw new Error(errorMessage(data, "会话列表查询失败"));
    }

    agentSessions.classList.remove("hidden");
    agentSessions.innerHTML = "";
    if (!data.sessions.length) {
      agentSessions.appendChild(
        createElement("p", "agent-sessions-empty", "还没有已保存的会话；勾选“保存会话”后运行 Agent 即可留档。"),
      );
      return;
    }
    data.sessions.forEach((session) => {
      const row = createElement("div", "agent-session-row");
      const info = createElement("div", "agent-session-info");
      info.append(
        createElement("strong", "agent-session-id", session.session_id),
        createElement(
          "span",
          "agent-session-meta",
          `${session.goal || "（无目标）"} · ${AGENT_STATUS_LABELS[session.status] || session.status} · ${session.saved_at}`,
        ),
      );
      const loadButton = createElement("button", "ghost-button", "载入");
      loadButton.type = "button";
      loadButton.addEventListener("click", () => {
        loadAgentSession(session.session_id).catch((error) => setMessage(error.message, true));
      });
      row.append(info, loadButton);
      agentSessions.appendChild(row);
    });
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    listAgentSessionsButton.disabled = false;
  }
}

const TASK_KIND_LABELS = {
  convert: "全文转换",
  scene_review: "机审",
  scene_rewrite: "局部重写",
  agent_run: "Agent 运行",
};

function formatSuccessRate(row) {
  return `${Math.round((row.success_rate || 0) * 1000) / 10}%`;
}

function renderMetricsSection(title, rows, isLlm) {
  const section = createElement("div", "metrics-section");
  section.appendChild(createElement("h4", "metrics-section-title", title));
  const entries = Object.entries(rows || {});
  if (!entries.length) {
    section.appendChild(
      createElement(
        "p",
        "agent-sessions-empty",
        isLlm ? "暂无 LLM 调用（本地模式全程不调用大模型）。" : "暂无任务记录。",
      ),
    );
    return section;
  }
  entries.forEach(([name, row]) => {
    const line = createElement("div", "metrics-row");
    line.appendChild(
      createElement("strong", "metrics-row-name", isLlm ? name : TASK_KIND_LABELS[name] || name),
    );
    const chips = createElement("div", "agent-metrics");
    chips.append(
      agentMetric(isLlm ? "调用" : "次数", row.calls),
      agentMetric("成功率", formatSuccessRate(row)),
      agentMetric("平均耗时", `${row.avg_ms} ms`),
      agentMetric("p95", `${row.p95_ms} ms`),
    );
    if (isLlm) {
      chips.append(
        agentMetric("Tokens", `${row.total_tokens}（入 ${row.prompt_tokens} / 出 ${row.completion_tokens}）`),
      );
      if (row.cache_hits !== undefined) {
        chips.append(agentMetric("缓存命中", row.cache_hits));
      }
    }
    if (row.last_error) {
      chips.append(agentMetric("最近错误", row.last_error));
    }
    line.appendChild(chips);
    section.appendChild(line);
  });
  return section;
}

async function refreshMetrics() {
  refreshMetricsButton.disabled = true;
  try {
    const response = await fetch("/api/metrics");
    const data = await response.json();

    if (!response.ok) {
      throw new Error(errorMessage(data, "指标查询失败"));
    }

    metricsEmptyState.classList.add("hidden");
    metricsContent.classList.remove("hidden");
    metricsContent.innerHTML = "";
    const llmRows =
      data.llm_overall && data.llm_overall.calls > 0
        ? { 合计: data.llm_overall, ...data.llm }
        : data.llm;
    metricsContent.appendChild(renderMetricsSection("LLM 调用（按子系统）", llmRows, true));
    metricsContent.appendChild(renderMetricsSection("任务统计", data.tasks, false));
    setMessage(`运行指标已刷新（统计自 ${data.since}）。`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    refreshMetricsButton.disabled = false;
  }
}

document.querySelector("#loadExampleButton").addEventListener("click", () => {
  loadExample().catch((error) => setMessage(error.message, true));
});
importNovelFileButton.addEventListener("click", () => {
  novelFileInput.click();
});
novelFileInput.addEventListener("change", () => {
  importNovelFile(novelFileInput.files[0]);
  novelFileInput.value = "";
});
document.querySelector("#convertButton").addEventListener("click", convertNovel);
document.querySelector("#analyzeCharactersButton").addEventListener("click", analyzeCharacters);
document.querySelector("#validateButton").addEventListener("click", validateYaml);
document.querySelector("#downloadButton").addEventListener("click", downloadYaml);
previewViewButton.addEventListener("click", () => setScriptView("preview"));
sourceViewButton.addEventListener("click", () => setScriptView("source"));
novelEditViewButton.addEventListener("click", () => setNovelView("edit"));
novelChapterViewButton.addEventListener("click", () => setNovelView("chapter"));
rewriteButtons.forEach((button) => {
  button.addEventListener("click", () => rewriteScene(button.dataset.rewriteOperation));
});
runReviewButton.addEventListener("click", runMachineReview);
downloadReviewReportButton.addEventListener("click", downloadReviewReport);
runAgentButton.addEventListener("click", runAgent);
listAgentSessionsButton.addEventListener("click", listAgentSessions);
refreshMetricsButton.addEventListener("click", refreshMetrics);
novelInput.addEventListener("input", updateChapterCount);

