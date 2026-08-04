from typing import Literal

from pydantic import BaseModel, Field

from .screenplay import DEFAULT_ADAPTATION_TYPE
from .screenplay import AdaptationType
from .screenplay import GlobalStoryState
from .screenplay import Screenplay
from .agent.models import AgentRunResult
from .agent.models import TeamRunResult
from .continuity import ContinuityFinding
from .scene_chat import ChatTurn
from .scene_chat import SceneChatMode
from .scene_rewrite import SceneRewriteMode
from .scene_rewrite import SceneRewriteOperation
from .scene_review import HumanVerdict
from .scene_review import ReviewReport


class ChapterPreviewRequest(BaseModel):
    novel_text: str = Field(min_length=1)


class ChapterPreviewItem(BaseModel):
    index: int
    title: str
    character_count: int
    preview: str


class ChapterPreviewResponse(BaseModel):
    chapter_count: int
    chapters: list[ChapterPreviewItem]


class GlobalStateRequest(BaseModel):
    novel_text: str = Field(min_length=1)


class GlobalStateResponse(BaseModel):
    global_state: GlobalStoryState


class NovelImportRequest(BaseModel):
    file_name: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)


class NovelImportResponse(BaseModel):
    file_name: str
    file_type: str
    title: str
    novel_text: str
    character_count: int


class ConvertRequest(BaseModel):
    novel_text: str = Field(min_length=1)
    title: str = ""
    genre: str = ""
    adaptation_type: AdaptationType = DEFAULT_ADAPTATION_TYPE
    mode: str = "demo"
    enable_review: bool = False


class ConvertResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    mode: str
    adaptation_type: AdaptationType
    review_report: ReviewReport | None = None
    security_warnings: list[str] = []
    # 非致命的转换告警，如"3/9 个片段失败已跳过"。剧本偏薄时用户需要知道原因。
    conversion_warnings: list[str] = []


ConvertJobStatus = Literal["queued", "running", "succeeded", "failed"]


class ConvertJobStartResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str


class ConvertJobStatusResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str
    result: ConvertResponse | None = None
    error: str = ""


class ValidateYamlRequest(BaseModel):
    yaml_text: str = Field(min_length=1)


class ValidateYamlResponse(BaseModel):
    valid: bool
    message: str
    screenplay: Screenplay | None = None


class SceneRewriteRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    operation: SceneRewriteOperation
    mode: SceneRewriteMode = "demo"
    character_id: str = ""
    tone: str = "更克制"
    # feedback 在库内已全线打通（agent/tools.py、mcp_server.py），REST 是唯一漏掉它的调用方。
    feedback: str = ""


class SceneRewriteResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    scene_id: str
    operation: SceneRewriteOperation
    mode: SceneRewriteMode
    message: str


class SceneChatRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    message: str = Field(min_length=1)
    # 无状态：历史由前端回传，与其它路由一致（项目里没有任何 REST 路由持有会话状态）。
    history: list[ChatTurn] = Field(default_factory=list)
    mode: SceneChatMode = "demo"
    scene_id: str = ""


class SceneChatResponse(BaseModel):
    reply: str
    mode: SceneChatMode
    # 只回话不改剧本时（refusal 非空）后四项为空，前端据此决定是否刷新预览。
    screenplay: Screenplay | None = None
    yaml_text: str = ""
    scene_id: str = ""
    operation: SceneRewriteOperation | None = None
    refusal: str = ""


class ProviderProfile(BaseModel):
    name: str
    active: bool
    # 密钥只出遮罩值（••••1234）；明文只存磁盘，不经 API 返回。
    fields: dict[str, str] = Field(default_factory=dict)
    has_api_key: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class ProviderListResponse(BaseModel):
    active: str
    profiles: list[ProviderProfile] = Field(default_factory=list)
    # 当前真正生效的配置（进程环境优先，与 LLMClient 的解析口径一致）。
    current: dict[str, str] = Field(default_factory=dict)
    # 被进程环境变量遮盖的字段：这些键写 .env 不生效，必须让用户看到。
    shadowed_fields: list[str] = Field(default_factory=list)
    # STORY2SCRIPT_DISABLE_DOTENV=1 时 .env 完全不被读取，切换会「写了但没效果」。
    dotenv_disabled: bool = False
    env_path: str = ""


class ProviderSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    # 只有白名单内的 AI_* 键会被接受，其余静默丢弃（见 provider_config）。
    fields: dict[str, str] = Field(default_factory=dict)
    activate: bool = False


class ProviderNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ProviderTestResponse(BaseModel):
    ok: bool
    message: str
    model: str = ""
    duration_ms: int = 0


class ExampleNovelResponse(BaseModel):
    title: str
    genre: str
    novel_text: str


class CharacterProfileRequest(BaseModel):
    novel_text: str = Field(min_length=1)
    mode: str = "demo"


class CharacterProfile(BaseModel):
    name: str
    role: str
    personality: str
    goal: str
    relationships: list[str]
    appearance_chapters: list[str]
    key_change: str


class CharacterProfileResponse(BaseModel):
    profiles: list[CharacterProfile]
    mode: str = "demo"


class SceneReviewRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    mode: str = "demo"
    auto_fix: bool = False
    threshold: float | None = None
    max_rounds: int | None = None
    scene_ids: list[str] = []


class SceneReviewResponse(BaseModel):
    report: ReviewReport
    screenplay: Screenplay | None = None
    yaml_text: str | None = None
    mode: str = "demo"
    message: str


class ReviewReportMergeRequest(BaseModel):
    report: ReviewReport
    verdicts: list[HumanVerdict]


class ReviewReportMergeResponse(BaseModel):
    report: ReviewReport


class AgentRunRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    goal: str = ""
    mode: str = "demo"
    threshold: float | None = None
    max_steps: int | None = None
    save_session: bool = False
    novel_text: str = ""


class AgentRunResponse(BaseModel):
    result: AgentRunResult
    screenplay: Screenplay
    yaml_text: str
    report: ReviewReport | None = None


class AgentJobStartResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str


class AgentJobStatusResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str
    result: AgentRunResponse | None = None
    error: str = ""


class AgentSessionListResponse(BaseModel):
    sessions: list[dict]


class TeamRunRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    goal: str = ""
    mode: str = "demo"
    threshold: float | None = None
    max_rounds: int | None = None
    max_steps_per_agent: int | None = None
    save_session: bool = False
    novel_text: str = ""


class TeamRunResponse(BaseModel):
    result: TeamRunResult
    screenplay: Screenplay
    yaml_text: str
    report: ReviewReport | None = None
    continuity_findings: list[ContinuityFinding] = []


class TeamJobStartResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str


class TeamJobStatusResponse(BaseModel):
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str
    result: TeamRunResponse | None = None
    error: str = ""


class AgentSessionDetailResponse(BaseModel):
    session_id: str
    saved_at: str = ""
    goal: str = ""
    status: str = ""
    result: AgentRunResult
    screenplay: Screenplay
    yaml_text: str
    report: ReviewReport | None = None


class MetricsSummaryResponse(BaseModel):
    generated_at: str
    since: str
    llm_overall: dict
    llm: dict
    tasks: dict


class MetricsEventsResponse(BaseModel):
    events: list[dict]


class RagQueryRequest(BaseModel):
    novel_text: str = Field(min_length=1)
    query: str = Field(min_length=1)
    mode: str = "demo"
    top_k: int | None = None
    before_chapter: int | None = None


class RagQueryResponse(BaseModel):
    retriever: str
    stats: dict
    hits: list[dict]


class JobListResponse(BaseModel):
    jobs: list[dict]

