from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "story2script" / "static"


def test_workbench_exposes_full_conversion_mode_selector() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="convertModeInput"' in html
    assert '<option value="demo" selected>本地</option>' in html
    assert '<option value="ai">AI</option>' in html


def test_workbench_sends_selected_full_conversion_mode() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const convertModeInput = document.querySelector("#convertModeInput");' in script
    assert "mode: convertModeInput.value" in script


def test_workbench_exposes_conversion_progress() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="conversionProgress"' in html
    assert 'id="conversionProgressStage"' in html
    assert 'id="conversionProgressPercent"' in html
    assert 'id="conversionProgressBar"' in html
    assert 'id="conversionProgressMessage"' in html


def test_workbench_uses_conversion_job_progress_api() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/convert/jobs"' in script
    assert "fetch(`/api/convert/jobs/${jobId}`)" in script
    assert "function setConversionProgress(" in script
    assert "async function waitForConversionJob(" in script


def test_workbench_exposes_screenplay_preview_toggle() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="scriptPreview"' in html
    assert 'id="previewViewButton"' in html
    assert 'id="sourceViewButton"' in html


def test_workbench_renders_structured_screenplay_preview() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function renderScreenplay(" in script
    assert "function setScriptView(" in script
    assert "showScreenplay(data.screenplay, data.yaml_text)" in script
    assert "line-dialogue" in script
    assert "line-action" in script


def test_workbench_exposes_novel_file_import() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="importNovelFileButton"' in html
    assert 'id="novelFileInput"' in html
    assert 'type="file"' in html
    assert ".txt,.text,.md,.markdown,.csv,.log,.epub,.mobi,.azw,.azw3,text/*" in html


def test_workbench_exposes_chapter_view() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="novelEditViewButton"' in html
    assert 'id="novelChapterViewButton"' in html
    assert 'id="chapterView"' in html


def test_workbench_chapter_detection_skips_empty_toc_headings() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # 章节识别要与后端一致：标题后没有正文（目录项）的不计入章节。
    assert "function detectChapters(" in script
    assert "if (content)" in script
    assert "detectChapters(novelInput.value)" in script
    assert "function renderChapterView(" in script


def test_workbench_rewrite_selects_character_by_name() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '<select id="rewriteCharacterInput">' in html
    assert "function populateCharacterOptions(" in script
    assert "populateCharacterOptions(screenplay)" in script


def test_workbench_reads_imported_novel_file() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const novelFileInput = document.querySelector("#novelFileInput");' in script
    assert "new FileReader()" in script
    assert 'new TextDecoder("utf-8", { fatal: true })' in script
    assert 'new TextDecoder("gb18030")' in script
    assert "reader.readAsArrayBuffer(file)" in script
    assert 'fetch("/api/novels/import"' in script
    assert "content_base64: arrayBufferToBase64(buffer)" in script
    assert "暂不支持直接解析 MOBI/AZW/AZW3" in script
    assert "novelInput.value = content" in script
    assert "updateChapterCount()" in script


def test_workbench_exposes_agent_panel() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "改编 Agent" in html
    assert 'id="agentGoalInput"' in html
    assert 'id="agentModeInput"' in html
    assert 'id="agentThresholdInput"' in html
    assert 'id="agentMaxStepsInput"' in html
    assert 'id="agentSaveSessionInput"' in html
    assert 'id="runAgentButton"' in html
    assert 'id="listAgentSessionsButton"' in html
    assert 'id="agentProgress"' in html
    assert 'id="agentTrace"' in html
    assert 'id="agentSessions"' in html


def test_workbench_runs_agent_via_job_api() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/agent/runs"' in script
    assert "fetch(`/api/agent/runs/${jobId}`)" in script
    assert "async function waitForAgentRun(" in script
    assert "function setAgentProgress(" in script
    assert "function renderAgentTrace(" in script
    assert "runAgentButton.addEventListener" in script


def test_workbench_agent_sessions_flow() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/agent/sessions")' in script
    assert "fetch(`/api/agent/sessions/${sessionId}`)" in script
    assert "function applyAgentRunResponse(" in script
    assert "listAgentSessionsButton.addEventListener" in script


def test_workbench_exposes_metrics_panel() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "运行指标" in html
    assert 'id="refreshMetricsButton"' in html
    assert 'id="metricsContent"' in html


def test_workbench_fetches_metrics_api() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/metrics")' in script
    assert "function renderMetricsSection(" in script
    assert "refreshMetricsButton.addEventListener" in script
