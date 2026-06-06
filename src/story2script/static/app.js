const titleInput = document.querySelector("#titleInput");
const genreInput = document.querySelector("#genreInput");
const adaptationTypeInput = document.querySelector("#adaptationTypeInput");
const convertModeInput = document.querySelector("#convertModeInput");
const novelInput = document.querySelector("#novelInput");
const yamlOutput = document.querySelector("#yamlOutput");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const convertButton = document.querySelector("#convertButton");
const analyzeCharactersButton = document.querySelector("#analyzeCharactersButton");
const importNovelFileButton = document.querySelector("#importNovelFileButton");
const novelFileInput = document.querySelector("#novelFileInput");
const sceneIdInput = document.querySelector("#sceneIdInput");
const rewriteModeInput = document.querySelector("#rewriteModeInput");
const rewriteCharacterInput = document.querySelector("#rewriteCharacterInput");
const rewriteToneInput = document.querySelector("#rewriteToneInput");
const rewriteButtons = document.querySelectorAll("[data-rewrite-operation]");
const profileGrid = document.querySelector("#profileGrid");
const profileEmptyState = document.querySelector("#profileEmptyState");
const supportedTextNovelFileExtensions = [".txt", ".text", ".md", ".markdown", ".csv", ".log"];
const unsupportedEbookFileExtensions = [".mobi", ".azw", ".azw3"];

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

async function convertNovel() {
  convertButton.disabled = true;
  const modeName = convertModeInput.value === "ai" ? "AI" : "本地";
  setMessage(`正在使用${modeName}模式生成 YAML 剧本……`);

  try {
    const response = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: titleInput.value,
        genre: genreInput.value,
        adaptation_type: adaptationTypeInput.value,
        mode: convertModeInput.value,
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
rewriteButtons.forEach((button) => {
  button.addEventListener("click", () => rewriteScene(button.dataset.rewriteOperation));
});
novelInput.addEventListener("input", updateChapterCount);

