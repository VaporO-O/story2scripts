from .agent import AdaptationTeam, AgentSessionStore
from .api_models import TeamJobStatusResponse
from .api_models import TeamRunRequest
from .api_models import TeamRunResponse
from .job_store import DurableJobStore
from .metrics import metrics
from .parser import parse_chapters
from .rag import build_story_knowledge
from .security import screen_agent_goal
from .story_state import extract_global_story_state
from .yaml_export import screenplay_from_yaml, screenplay_to_yaml


class TeamJobStore(DurableJobStore):
    """多智能体协作任务队列：持久化、可恢复；独立线程池，
    避免与单体 Agent、转换任务互相阻塞。"""

    kind = "team_run"
    request_model = TeamRunRequest
    result_model = TeamRunResponse
    response_model = TeamJobStatusResponse
    queued_stage = "等待执行"
    queued_message = "协作任务已创建，等待后端开始处理。"
    complete_stage = "协作完成"
    fail_stage = "协作失败"

    def _run(self, job_id: str) -> None:
        request = self._request_for(job_id)
        reached_run = False
        try:
            self._update(job_id, progress=5, stage="安全检查")
            screen_agent_goal(request.goal)

            self._update(job_id, progress=10, stage="解析剧本")
            try:
                screenplay = screenplay_from_yaml(request.yaml_text)
            except Exception as exc:
                raise ValueError(f"YAML 校验失败：{exc}") from exc

            self._update(
                job_id,
                progress=15,
                stage="团队启动",
                message="正在组建审校 / 一致性 / 改编三个专职代理。",
            )
            team = AdaptationTeam(
                mode=request.mode,
                max_rounds=request.max_rounds,
                threshold=request.threshold,
                max_steps_per_agent=request.max_steps_per_agent,
            )

            def progress_cb(round_no: int, max_rounds: int, note: str) -> None:
                progress = 15 + int(75 * round_no / max(1, max_rounds))
                self._update(job_id, progress=progress, stage="协作进行中", message=note)

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

            session_store = AgentSessionStore() if request.save_session else None
            reached_run = True
            outcome = team.run(
                screenplay,
                goal=request.goal,
                progress_cb=progress_cb,
                session_store=session_store,
                knowledge=knowledge,
            )

            self._update(job_id, progress=95, stage="导出结果", message="正在导出剧本与协作轨迹。")
            result = TeamRunResponse(
                result=outcome.result,
                screenplay=outcome.screenplay,
                yaml_text=screenplay_to_yaml(outcome.screenplay),
                report=outcome.report,
                continuity_findings=outcome.continuity_findings,
            )
            self._complete(job_id, result)
        except ValueError as exc:
            self._fail(job_id, str(exc))
            if not reached_run:
                self._record_pre_run_failure(request.mode, str(exc))
        except Exception as exc:
            self._fail(job_id, f"协作任务失败：{exc}")
            if not reached_run:
                self._record_pre_run_failure(request.mode, f"协作任务失败：{exc}")

    def _complete_message(self, result: TeamRunResponse) -> str:
        return result.result.message or "协作完成。"

    @staticmethod
    def _record_pre_run_failure(mode: str, error: str) -> None:
        # 协作循环内的成败由 AdaptationTeam.run 记录；这里只补没进入循环就失败的
        # 任务（目标被安全拦截、YAML 解析失败、知识库构建失败），避免漏计。
        metrics.record_task(
            "team_run",
            mode=mode,
            ok=False,
            error=error,
            extra={"status": "not_started"},
        )


team_jobs = TeamJobStore()
