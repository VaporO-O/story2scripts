from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

from .agent import AdaptationAgent, AgentSessionStore
from .api_models import AgentJobStatusResponse
from .api_models import AgentRunRequest
from .api_models import AgentRunResponse
from .api_models import ConvertJobStatus
from .metrics import metrics
from .parser import parse_chapters
from .rag import build_story_knowledge
from .story_state import extract_global_story_state
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml


@dataclass
class AgentJob:
    job_id: str
    status: ConvertJobStatus
    progress: int
    stage: str
    message: str
    request: AgentRunRequest
    result: AgentRunResponse | None = None
    error: str = ""


class AgentJobStore:
    """Agent 运行任务存储：独立线程池，避免与转换任务互相阻塞。"""

    def __init__(self) -> None:
        self._jobs: dict[str, AgentJob] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    def create(self, request: AgentRunRequest) -> AgentJobStatusResponse:
        job = AgentJob(
            job_id=str(uuid4()),
            status="queued",
            progress=0,
            stage="等待执行",
            message="Agent 任务已创建，等待后端开始处理。",
            request=request,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id)
        return self.snapshot(job.job_id)

    def snapshot(self, job_id: str) -> AgentJobStatusResponse:
        with self._lock:
            job = self._jobs[job_id]
            return AgentJobStatusResponse(
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
        reached_run = False
        try:
            self._update(job_id, progress=10, stage="解析剧本")
            try:
                screenplay = screenplay_from_yaml(request.yaml_text)
            except Exception as exc:
                raise ValueError(f"YAML 校验失败：{exc}") from exc

            self._update(
                job_id,
                progress=15,
                stage="Agent 启动",
                message="正在初始化改编代理并执行首轮审校。",
            )
            agent = AdaptationAgent(
                mode=request.mode,
                max_steps=request.max_steps,
                threshold=request.threshold,
            )

            def progress_cb(step: int, max_steps: int, note: str) -> None:
                progress = 15 + int(75 * step / max(1, max_steps))
                self._update(job_id, progress=progress, stage="Agent 执行中", message=note)

            session_store = AgentSessionStore() if request.save_session else None
            knowledge = None
            if request.novel_text.strip():
                try:
                    kb_chapters = parse_chapters(request.novel_text)
                    knowledge = build_story_knowledge(
                        kb_chapters,
                        extract_global_story_state(kb_chapters),
                        mode=request.mode,
                    )
                except ValueError as exc:
                    raise ValueError(f"小说前文知识库构建失败：{exc}") from exc
            reached_run = True
            outcome = agent.run(
                screenplay,
                goal=request.goal,
                progress_cb=progress_cb,
                session_store=session_store,
                knowledge=knowledge,
            )

            self._update(job_id, progress=95, stage="导出结果", message="正在导出剧本与轨迹。")
            result = AgentRunResponse(
                result=outcome.result,
                screenplay=outcome.screenplay,
                yaml_text=screenplay_to_yaml(outcome.screenplay),
                report=outcome.report,
            )
            self._complete(job_id, result)
        except ValueError as exc:
            self._fail(job_id, str(exc))
            if not reached_run:
                self._record_pre_run_failure(request.mode, str(exc))
        except Exception as exc:
            self._fail(job_id, f"Agent 任务失败：{exc}")
            if not reached_run:
                self._record_pre_run_failure(request.mode, f"Agent 任务失败：{exc}")

    @staticmethod
    def _record_pre_run_failure(mode: str, error: str) -> None:
        # Agent 循环内的成败（含异常）由 AdaptationAgent.run 记录；这里只补
        # 没进入循环就失败的任务（YAML 解析失败、模式非法），避免漏计。
        metrics.record_task(
            "agent_run",
            mode=mode,
            ok=False,
            error=error,
            extra={"status": "not_started"},
        )

    def _request_for(self, job_id: str) -> AgentRunRequest:
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

    def _complete(self, job_id: str, result: AgentRunResponse) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.progress = 100
            job.stage = "执行完成"
            job.message = result.result.message or "Agent 执行完成。"
            job.result = result
            job.error = ""

    def _fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.stage = "执行失败"
            job.message = error
            job.error = error


agent_jobs = AgentJobStore()
