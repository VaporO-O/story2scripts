
from .agent import AdaptationAgent, AgentSessionStore
from .api_models import AgentJobStatusResponse
from .api_models import AgentRunRequest
from .api_models import AgentRunResponse
from .job_store import DurableJobStore
from .metrics import metrics
from .parser import parse_chapters
from .rag import build_story_knowledge
from .story_state import extract_global_story_state
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml


class AgentJobStore(DurableJobStore):
    """Agent 运行任务队列：持久化、可恢复；与转换任务共用一个 SQLite 库，
    但保持独立线程池，避免与转换任务互相阻塞。"""

    kind = "agent_run"
    request_model = AgentRunRequest
    result_model = AgentRunResponse
    response_model = AgentJobStatusResponse
    queued_stage = "等待执行"
    queued_message = "Agent 任务已创建，等待后端开始处理。"
    complete_stage = "执行完成"
    fail_stage = "执行失败"

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

    def _complete_message(self, result: AgentRunResponse) -> str:
        return result.result.message or "Agent 执行完成。"

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


agent_jobs = AgentJobStore()
