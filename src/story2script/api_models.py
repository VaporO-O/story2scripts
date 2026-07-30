from typing import Literal

from pydantic import BaseModel, Field

from .screenplay import DEFAULT_ADAPTATION_TYPE
from .screenplay import AdaptationType
from .screenplay import GlobalStoryState
from .screenplay import Screenplay
from .agent.models import AgentRunResult
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


class SceneRewriteResponse(BaseModel):
    screenplay: Screenplay
    yaml_text: str
    scene_id: str
    operation: SceneRewriteOperation
    mode: SceneRewriteMode
    message: str


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

