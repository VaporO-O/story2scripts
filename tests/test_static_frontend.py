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

    # 「按名字指定角色」的能力没有消失，而是从下拉框搬进了对话：用户直接写名字，
    # 服务端按 name 精确匹配回 id（见 test_scene_chat.py 的解析测试），
    # 所以前端不再维护角色清单。
    assert 'id="rewriteCharacterInput"' not in html
    assert "function populateCharacterOptions(" not in script
    assert 'id="chatInput"' in html


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


def test_workbench_metrics_panel_shows_cache_hits() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "缓存命中" in script
    assert "row.cache_hits" in script


def test_workbench_shows_security_warnings() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "data.security_warnings" in script
    assert "疑似提示注入" in script


def test_workbench_exposes_team_panel() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "多智能体协作" in html
    assert 'id="teamGoalInput"' in html
    assert 'id="teamModeInput"' in html
    assert 'id="teamThresholdInput"' in html
    assert 'id="teamMaxRoundsInput"' in html
    assert 'id="teamSaveSessionInput"' in html
    assert 'id="runTeamButton"' in html
    assert 'id="listTeamSessionsButton"' in html
    assert 'id="teamProgress"' in html
    assert 'id="teamSummary"' in html
    assert 'id="teamFindings"' in html
    assert 'id="teamTrace"' in html
    assert 'id="teamMessages"' in html
    assert 'id="teamSessions"' in html


def test_workbench_runs_team_via_job_api() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/agent/teams/runs"' in script
    assert "fetch(`/api/agent/teams/runs/${jobId}`)" in script
    assert 'fetch("/api/agent/teams/sessions")' in script
    assert "async function waitForTeamRun(" in script
    assert "function setTeamProgress(" in script
    assert "function applyTeamRunResponse(" in script
    assert "runTeamButton.addEventListener" in script
    assert "listTeamSessionsButton.addEventListener" in script


def test_workbench_renders_role_tagged_team_trace() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # 协作时间线复用单体 Agent 的步骤渲染，靠 container 参数与 role 标签区分
    assert "function renderAgentTrace(trace, container = agentTrace)" in script
    assert "renderAgentTrace(data.result.trace, teamTrace)" in script
    assert "agent-step-role" in script
    assert "function renderTeamFindings(" in script
    assert "function renderTeamMessages(" in script
    assert "const ROLE_LABELS" in script


def test_workbench_exposes_view_navigation() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="viewWorkbenchButton"' in html
    assert 'id="viewAgentsButton"' in html
    assert 'id="viewMetricsButton"' in html
    assert 'id="viewWorkbench"' in html
    assert 'id="viewAgents"' in html
    assert 'id="viewMetrics"' in html


def test_workbench_switches_views() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function setActiveView(" in script
    assert 'viewWorkbenchButton.addEventListener' in script
    assert 'viewAgentsButton.addEventListener' in script
    assert 'viewMetricsButton.addEventListener' in script
    # 转换时切回工作台，让进度条与结果在同一视野
    assert 'setActiveView("workbench")' in script


def test_workbench_streams_scenes_over_sse() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "async function streamConversionJob(" in script
    assert "/events" in script
    # 手写 SSE 解析：EventSource 无法设置请求头，而鉴权中间件要求
    # Authorization: Bearer，用它就只能 401。
    # 只断言"没被使用"——代码注释里会提到它，说明为什么不用。
    assert "new EventSource(" not in script
    assert "getReader()" in script
    assert "TextDecoder" in script
    assert 'buffer.indexOf("\\n\\n")' in script


def test_workbench_renders_streamed_scenes_incrementally() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # renderScreenplay 原来开头就 innerHTML = "" 全量重绘，追加一场要重画全部
    assert "function renderScreenplayHeader(" in script
    assert "function appendStreamedScene(" in script
    assert "function beginSceneStream(" in script
    assert "function resetSceneStream(" in script
    # 场景数标签在流式过程中一直增长，要可变
    assert "sceneCountTag" in script


def test_workbench_keeps_polling_as_stream_fallback() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # SSE 连不上就退回 1Hz 轮询，转换本身不受影响
    assert "await waitForConversionJob(jobId)" in script
    assert "streamedToEnd" in script
    # 流式期间 yaml_text 还没到，门禁不能再只看 yamlOutput.value
    assert "hasStreamedContent" in script


def test_workbench_exposes_scene_chat() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="chatInput"' in html
    assert 'id="chatSendButton"' in html
    assert 'id="chatLog"' in html
    assert 'id="chatModeInput"' in html
    # 场景 ID 从输入框变成 chip + 内部状态，点剧本里的 SCENE 徽章切换
    assert 'id="chatSceneChip"' in html


def test_scene_chat_replaces_preset_rewrite_buttons() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # 六个预设操作按钮已被对话取代；操作枚举仍在后端白名单里，前端不再直接提交
    assert "data-rewrite-operation" not in html
    assert 'id="sceneIdInput"' not in html
    assert 'id="rewriteToneInput"' not in html


def test_workbench_sends_chat_rewrite_requests() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/scenes/chat"' in script
    assert "function renderChatMessages(" in script
    assert "function sendChatMessage(" in script
    # class="message" 被负向断言占用（见 test_workbench_status_bar_is_global），
    # 聊天条目用 chat-message 前缀
    assert "chat-message-user" in script
    # 本地模式会丢弃原话里的细微差别，界面必须说明，不能让用户以为对话没生效
    assert "function updateChatModeHint(" in script


def test_workbench_exposes_profiles_view() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # 人物小传只依赖小说原文、与剧本流程无关，独立成第 4 个分区
    assert 'id="viewProfilesButton"' in html
    assert 'id="viewProfiles"' in html
    assert 'id="reanalyzeCharactersButton"' in html
    assert 'id="profileGrid"' in html


def test_workbench_switches_to_profiles_after_analysis() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # 触发按钮留在小说面板（它读 novelInput），结果在人物小传视图：分析完要把用户带过去
    assert 'viewProfilesButton.addEventListener' in script
    assert 'setActiveView("profiles")' in script
    assert "reanalyzeCharactersButton.addEventListener" in script


def test_review_tools_render_below_preview() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # 机审是对预览内容的操作，必须排在预览之后而不是抢占预览上方的空间
    preview_at = html.index('id="scriptPreview"')
    review_at = html.index('class="review-tools"')
    assert preview_at < review_at


def test_screenplay_preview_is_flexible() -> None:
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    # 预览/空状态/YAML 三块共用一条伸缩规则；硬钉死高度会让剧本空间上不去，
    # 三处不同步还会导致切换视图时面板跳动
    assert "height: 354px" not in css
    assert ".script-panel > .script-preview," in css
    assert ".script-panel > .empty-state," in css


def test_workbench_status_bar_is_global() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # 状态栏从剧本面板内部（绝对定位）提到全局常规流，切换分区后仍可见
    assert 'id="message" class="status-bar"' in html
    assert 'class="message"' not in html


def test_workbench_shows_screenplay_status_in_agents_view() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="screenplayStatusText"' in html
    assert 'id="backToWorkbenchButton"' in html
    assert "function updateScreenplayStatus(" in script


def test_workbench_explains_review_scoring_rules() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "评分规则" in html
    assert "等权算术平均" in html
    assert "AI_REVIEW_THRESHOLD" in html
    # 两种模式不可比，必须写明，否则用户会误以为同标尺
    assert "不可直接比较" in html


def test_workbench_renders_review_score_detail() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function renderReviewDetail(" in script
    assert "renderReviewDetail(scene.id)" in script
    # 标签与后端 CRITERIA_LABELS 一致
    assert 'dramatization: "戏剧化程度"' in script
    assert 'dialogue_conflict: "对白推动冲突"' in script
    assert 'character_voice: "人物语气一致性"' in script
    # 通过的场景把建议操作显示为"可选优化方向"
    assert "可选优化方向" in script


def test_team_trace_appends_into_its_own_container() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # 此前写死 agentTrace.appendChild，导致协作时间线渲染进了改编 Agent 面板
    assert "container.appendChild(item)" in script
    assert "agentTrace.appendChild(item)" not in script
