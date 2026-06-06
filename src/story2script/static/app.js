const titleInput = document.querySelector("#titleInput");
const genreInput = document.querySelector("#genreInput");
const adaptationTypeInput = document.querySelector("#adaptationTypeInput");
const novelInput = document.querySelector("#novelInput");
const yamlOutput = document.querySelector("#yamlOutput");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const convertButton = document.querySelector("#convertButton");
const analyzeCharactersButton = document.querySelector("#analyzeCharactersButton");
const sceneIdInput = document.querySelector("#sceneIdInput");
const rewriteModeInput = document.querySelector("#rewriteModeInput");
const rewriteCharacterInput = document.querySelector("#rewriteCharacterInput");
const rewriteToneInput = document.querySelector("#rewriteToneInput");
const rewriteButtons = document.querySelectorAll("[data-rewrite-operation]");
const profileGrid = document.querySelector("#profileGrid");
const profileEmptyState = document.querySelector("#profileEmptyState");

function setMessage(text, isError = false) {
  message.textContent = text;
  message.style.color = isError ? "#c33b26" : "";
}

function updateChapterCount() {
  const chapters = novelInput.value.match(/^\s*(第.+章.*|chapter\s+\d+.*)$/gim) || [];
  document.querySelector("#chapterCount").textContent = `已识别 ${chapters.length} 个章节`;
}

function errorMessage(data, fallback) {
  return typeof data.detail === "string" ? data.detail : fallback;
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
  novelInput.value = data.novel_text;
  updateChapterCount();
  setMessage("示例小说已填入。");
}

async function convertNovel() {
  convertButton.disabled = true;
  setMessage("正在解析小说并生成 YAML 剧本……");

  try {
    const response = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: titleInput.value,
        genre: genreInput.value,
        adaptation_type: adaptationTypeInput.value,
        novel_text: novelInput.value,
      }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "转换失败");
    }

    yamlOutput.value = data.yaml_text;
    emptyState.classList.add("hidden");
    yamlOutput.classList.remove("hidden");
    setMessage(
      `已生成 ${data.screenplay.scenes.length} 个场景，改编类型：${data.adaptation_type}，当前模式：${data.mode}。`,
    );
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    convertButton.disabled = false;
  }
}

async function analyzeCharacters() {
  analyzeCharactersButton.disabled = true;
  setMessage("正在分析人物小传……");

  try {
    const response = await fetch("/api/characters/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ novel_text: novelInput.value }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "人物分析失败");
    }

    renderProfiles(data.profiles);
    setMessage(`已提取 ${data.profiles.length} 个人物小传。`);
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

    yamlOutput.value = data.yaml_text;
    emptyState.classList.add("hidden");
    yamlOutput.classList.remove("hidden");
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

document.querySelector("#loadExampleButton").addEventListener("click", () => {
  loadExample().catch((error) => setMessage(error.message, true));
});
document.querySelector("#convertButton").addEventListener("click", convertNovel);
document.querySelector("#analyzeCharactersButton").addEventListener("click", analyzeCharacters);
document.querySelector("#validateButton").addEventListener("click", validateYaml);
document.querySelector("#downloadButton").addEventListener("click", downloadYaml);
rewriteButtons.forEach((button) => {
  button.addEventListener("click", () => rewriteScene(button.dataset.rewriteOperation));
});
novelInput.addEventListener("input", updateChapterCount);

