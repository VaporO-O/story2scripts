from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from uuid import uuid4

from .api_models import ConvertJobStatus
from .api_models import ConvertJobStatusResponse
from .api_models import ConvertRequest
from .api_models import ConvertResponse
from .converter import get_converter
from .metrics import metrics
from .parser import parse_chapters
from .scene_review import review_and_improve
from .yaml_export import screenplay_to_yaml


@dataclass
class ConversionJob:
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str
    request: ConvertRequest
    result: ConvertResponse | None = None
    error: str = ""


class ConversionJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, ConversionJob] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    def create(self, request: ConvertRequest) -> ConvertJobStatusResponse:
        job = ConversionJob(
            job_id=str(uuid4()),
            status="queued",
            progress=0,
            stage="等待转换",
            message="转换任务已创建，等待后端开始处理。",
            request=request,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id)
        return self.snapshot(job.job_id)

    def snapshot(self, job_id: str) -> ConvertJobStatusResponse:
        with self._lock:
            job = self._jobs[job_id]
            return ConvertJobStatusResponse(
                job_id=job.job_id,
                status=job.status,
                progress=job.progress,
                stage=job.stage,
                message=job.message,
                result=job.result,
                error=job.error,
            )

    def has_job(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def _run(self, job_id: str) -> None:
        request = self._request_for(job_id)
        started = perf_counter()

        def record(ok: bool, error: str = "", scene_count: int = 0) -> None:
            metrics.record_task(
                "convert",
                mode=request.mode,
                duration_ms=int((perf_counter() - started) * 1000),
                ok=ok,
                error=error,
                extra={"source": "job", "scene_count": scene_count},
            )

        try:
            self._update(job_id, status="running", progress=10, stage="解析章节")
            chapters = parse_chapters(request.novel_text)

            self._update(
                job_id,
                progress=25,
                stage="准备转换器",
                message=f"已识别 {len(chapters)} 个章节，正在准备{_mode_name(request.mode)}转换器。",
            )
            converter = get_converter(request.mode)

            self._update(
                job_id,
                progress=45,
                stage="生成剧本",
                message=_generation_message(converter.mode),
            )
            screenplay = converter.convert(
                chapters=chapters,
                title=request.title,
                genre=request.genre,
                adaptation_type=request.adaptation_type,
            )

            review_report = None
            if request.enable_review:
                self._update(
                    job_id,
                    progress=70,
                    stage="质量审校",
                    message="正在按四项标准审校场景，并自动修正不达标场景。",
                )
                screenplay, review_report = review_and_improve(
                    screenplay,
                    mode=converter.mode,
                    progress_cb=lambda note: self._update(
                        job_id, progress=70, stage="质量审校", message=note
                    ),
                )

            self._update(
                job_id,
                progress=90,
                stage="导出 YAML",
                message="剧本结构已生成，正在导出可编辑 YAML。",
            )
            result = ConvertResponse(
                screenplay=screenplay,
                yaml_text=screenplay_to_yaml(screenplay),
                mode=converter.mode,
                adaptation_type=request.adaptation_type,
                review_report=review_report,
            )
            self._complete(job_id, result)
            record(ok=True, scene_count=len(result.screenplay.scenes))
        except ValueError as exc:
            self._fail(job_id, str(exc))
            record(ok=False, error=str(exc))
        except Exception as exc:
            self._fail(job_id, f"转换任务失败：{exc}")
            record(ok=False, error=f"转换任务失败：{exc}")

    def _request_for(self, job_id: str) -> ConvertRequest:
        with self._lock:
            return self._jobs[job_id].request

    def _update(
        self,
        job_id: str,
        *,
        status: ConvertJobStatus = "running",
        progress: int,
        stage: str,
        message: str = "",
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.progress = progress
            job.stage = stage
            job.message = message or stage

    def _complete(self, job_id: str, result: ConvertResponse) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.progress = 100
            job.stage = "转换完成"
            job.message = f"已生成 {len(result.screenplay.scenes)} 个场景。"
            job.result = result
            job.error = ""

    def _fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.stage = "转换失败"
            job.message = error
            job.error = error


def _mode_name(mode: str) -> str:
    return "AI" if mode == "ai" else "本地"


def _generation_message(mode: str) -> str:
    if mode == "ai":
        return "正在调用 AI 生成 Screenplay JSON，并等待后端 Schema 校验。"
    return "正在使用本地规则拆分场景、生成人物和戏剧要素。"


conversion_jobs = ConversionJobStore()
