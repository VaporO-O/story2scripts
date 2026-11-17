from .job_store import DurableJobStore
from .metrics import metrics
from .plot_models import PlotJobRequest, PlotJobStatus, ScriptRevision
from .plot_revision import check_base, generate_revision, load_plan
from .plot_store import load_record
from .yaml_export import screenplay_from_yaml


class PlotJobStore(DurableJobStore):
    kind = "plot"
    request_model = PlotJobRequest
    result_model = ScriptRevision
    response_model = PlotJobStatus
    queued_stage = "等待剧情改写"
    queued_message = "候选版本任务已保存，原剧本保持可用。"
    complete_stage = "候选版本已生成"
    fail_stage = "剧情改写中断"

    def create(self, request, *, idempotency_key=""):
        plan, _ = load_plan(request.plan_id)
        check_base(plan, screenplay_from_yaml(request.yaml_text))
        if not request.confirm_logic:
            raise ValueError("请先查看并确认初始事实与假设，再生成候选版本。")
        return super().create(request, idempotency_key=idempotency_key)

    def _run(self, job_id):
        request = self._request_for(job_id)
        try:
            result = generate_revision(
                request.plan_id,
                job_id,
                progress=lambda progress, stage, note: self._update(
                    job_id, progress=progress, stage=stage, message=note
                ),
            )
            self._complete(job_id, result)
            metrics.record_task(
                "plot_revision",
                mode="ai",
                ok=not result.issues,
                duration_ms=int(result.duration_seconds * 1000),
                extra={
                    "calls": result.calls,
                    "changed_scenes": len(result.changes),
                    "issues": len(result.issues),
                },
            )
        except Exception as exc:
            self._fail(job_id, str(exc))
            metrics.record_task("plot_revision", mode="ai", ok=False, error=str(exc))

    def _complete_message(self, result):
        if result.issues:
            return f"候选存在 {len(result.issues)} 项未解决问题，暂不能采用；原剧本未变。"
        return (
            f"已检查 {len(result.scanned_scene_ids)} 场，修改 {len(result.changes)} 场，等待采用。"
        )

    def resume(self, job_id):
        request = self._request_for(job_id)
        plan, base = load_plan(request.plan_id)
        check_base(plan, base)
        if not request.confirm_logic:
            raise ValueError("旧任务缺少事件账本确认，请重新规划。")
        try:
            cp = load_record("checkpoint", job_id)
        except KeyError:
            cp = {}
        if cp.get("calls", 0) >= plan.max_calls or cp.get("elapsed", 0) >= plan.timeout_seconds:
            raise ValueError("任务预算已耗尽，请调整预算后重新规划。")
        return super().resume(job_id)


plot_jobs = PlotJobStore()
