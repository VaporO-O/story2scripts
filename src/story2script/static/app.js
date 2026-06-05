const titleInput = document.querySelector("#titleInput");
const genreInput = document.querySelector("#genreInput");
const novelInput = document.querySelector("#novelInput");
const yamlOutput = document.querySelector("#yamlOutput");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const convertButton = document.querySelector("#convertButton");

function setMessage(text, isError = false) {
  message.textContent = text;
  message.style.color = isError ? "#c33b26" : "";
}

function updateChapterCount() {
  const chapters = novelInput.value.match(/^\s*(第.+章.*|chapter\s+\d+.*)$/gim) || [];
  document.querySelector("#chapterCount").textContent = `已识别 ${chapters.length} 个章节`;
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
    setMessage(`已生成 ${data.screenplay.scenes.length} 个场景，当前模式：${data.mode}。`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    convertButton.disabled = false;
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
document.querySelector("#validateButton").addEventListener("click", validateYaml);
document.querySelector("#downloadButton").addEventListener("click", downloadYaml);
novelInput.addEventListener("input", updateChapterCount);

