"""Durable AI authoring tasks with per-response checkpoints and cumulative budgets."""

import hashlib
import sqlite3

from .job_store import DurableJobStore, JobStorageError
from .llm_client import LLMClient
from .metrics import metrics
from .plot_revision import CallBudget, _prompt
from .plot_state import screenplay_hash
from .plot_store import load_record, save_record
from .studio import annotated_emotions
from .studio_models import STUDIO_REQUESTS, StudioJobRequest, StudioJobResult, StudioJobStatus
from .studio_tasks import _result, emotion_analysis, propose_structure, recheck_script
from .yaml_export import screenplay_from_yaml

LABELS = {"structure": "AI 分幕／分集", "emotions": "AI 情绪分析", "recheck": "编辑后重新核验"}


def load_checkpoint(job_id, db_path=None):
    try:
        return load_record("studio_checkpoint", job_id, db_path=db_path)
    except KeyError:
        return {}
    except (sqlite3.Error, OSError) as exc:
        raise JobStorageError("无法读取创作任务检查点，请检查存储后重试。") from exc


def save_checkpoint(job_id, value):
    try:
        save_record("studio_checkpoint", job_id, value)
    except (sqlite3.Error, OSError) as exc:
        raise JobStorageError("创作任务检查点未保存，执行已停止；不能报告成功。") from exc


class CheckpointBudget(CallBudget):
    def __init__(self, client, request, checkpoint, save, progress, expected):
        interrupted = checkpoint.pop("in_flight", None)
        if interrupted:
            # The process died before observing completion. Conservatively charge
            # the reserved call window, rather than refunding unknown execution time.
            checkpoint["elapsed"] = max(
                checkpoint.get("elapsed", 0),
                interrupted["elapsed_before"] + interrupted["reserved_seconds"],
            )
            save(checkpoint)
        super().__init__(client, request.max_calls, request.timeout_seconds, checkpoint, save)
        self.progress = progress
        self.expected = expected
        checkpoint.setdefault("responses", {})

    def call(self, task, model, context, prompt_id):
        self.last_response_key = None
        key = hashlib.sha256(_prompt(task, model, context).encode("utf-8")).hexdigest()
        responses = self.checkpoint["responses"]
        if key in responses:
            self.persist()
            value = model.model_validate(responses[key])
            self.last_response_key = key
            self.progress(
                min(90, 10 + 80 * len(responses) // self.expected),
                "复用检查点",
                "已保存的模型响应无需重复调用。",
            )
            return value
        self.progress(
            min(89, 10 + 80 * len(responses) // self.expected),
            "模型处理中",
            f"{context.get('scene', {}).get('id', '')} · 已保存 {len(responses)}/{self.expected} 步，已用 {self.checkpoint.get('calls', 0)} 次调用。",
        )
        self.persist()
        remaining = max(0, self.timeout - self.checkpoint.get("elapsed", 0))
        self.checkpoint["in_flight"] = {
            "elapsed_before": self.checkpoint.get("elapsed", 0),
            "reserved_seconds": min(getattr(self.client, "timeout_seconds", remaining), remaining),
        }
        self.persist()
        try:
            value = super().call(task, model, context, prompt_id)
        finally:
            self.checkpoint.pop("in_flight", None)
            self.persist()
        responses[key] = value.model_dump(mode="json")
        self.persist()
        self.last_response_key = key
        self.progress(
            min(90, 10 + 80 * len(responses) // self.expected),
            "已保存检查点",
            f"已保存 {len(responses)}/{self.expected} 步。",
        )
        return value


class StudioJobStore(DurableJobStore):
    kind = "studio"
    request_model = StudioJobRequest
    result_model = StudioJobResult
    response_model = StudioJobStatus
    queued_stage = "等待创作任务"
    queued_message = "任务已保存，可以离开当前页面，稍后恢复进度。"
    complete_stage = "创作任务完成"
    fail_stage = "创作任务中断"

    def snapshot(self, job_id):
        result = super().snapshot(job_id)
        row = self._row(job_id)
        # Old/corrupt requests still expose their persisted failure state.
        try:
            request = self.request_model.model_validate_json(row["request_json"])
        except ValueError:
            return result
        cp = load_checkpoint(job_id, self._job_path(job_id))
        return result.model_copy(
            update={
                "attempts": row["attempts"],
                "calls": cp.get("calls", 0),
                "max_calls": request.max_calls,
                "elapsed_seconds": round(cp.get("elapsed", 0), 2),
                "timeout_seconds": request.timeout_seconds,
            }
        )

    def create(self, request, *, idempotency_key=""):
        typed = STUDIO_REQUESTS[request.operation].model_validate(request.parameters)
        script = screenplay_from_yaml(typed.yaml_text)
        if request.operation == "structure" and typed.count > len(script.scenes):
            raise ValueError("幕/集数量不能超过场景数。")
        if request.operation == "emotions":
            notes = annotated_emotions(script, typed.scene_ids)
            if sum(len(n["quote"]) for n in notes) > 60000:
                raise ValueError("请缩小情绪分析范围至当前场景，所选对白过长。")
        needed = (
            len(script.editing.dirty_scene_ids) + 1
            if request.operation == "recheck"
            and script.editing
            and script.plot_state
            and script.plot_state.logic
            else 1
        )
        if request.max_calls < needed:
            raise ValueError(f"本次任务至少需要 {needed} 次调用预算。")
        return super().create(request, idempotency_key=idempotency_key)

    def _run(self, job_id):
        request = self._request_for(job_id)
        typed = STUDIO_REQUESTS[request.operation].model_validate(request.parameters)
        base_hash = screenplay_hash(screenplay_from_yaml(typed.yaml_text))
        cp = load_checkpoint(job_id)
        if cp and (cp.get("base_hash") != base_hash or cp.get("operation") != request.operation):
            raise ValueError("检查点与本次任务输入不一致。")
        if cp.get("result"):
            self._complete(job_id, StudioJobResult.model_validate(cp["result"]))
            return
        cp.update(base_hash=base_hash, operation=request.operation)
        save_checkpoint(job_id, cp)
        self._update(
            job_id,
            progress=5,
            stage=LABELS[request.operation],
            message="读取冻结输入及已有检查点。",
        )
        script = screenplay_from_yaml(typed.yaml_text)
        if request.operation == "emotions" and not annotated_emotions(script, typed.scene_ids):
            result = StudioJobResult(
                operation="emotions",
                base_hash=base_hash,
                payload={"hash": base_hash, "notes": [], "mode": "ai"},
                calls=cp.get("calls", 0),
                duration_seconds=cp.get("elapsed", 0),
            )
            cp["result"] = result.model_dump(mode="json")
            save_checkpoint(job_id, cp)
            self._complete(job_id, result)
            return
        expected = (
            (
                len(script.editing.dirty_scene_ids)
                if script.editing and script.plot_state and script.plot_state.logic
                else 0
            )
            + 1
            if request.operation == "recheck"
            else 1
        )
        client = LLMClient(usage_label=LABELS[request.operation])
        budget = CheckpointBudget(
            client,
            request,
            cp,
            lambda value: save_checkpoint(job_id, value),
            lambda p, s, m: self._update(job_id, progress=p, stage=s, message=m),
            expected,
        )
        try:
            if request.operation == "structure":
                payload = _result(propose_structure(typed, budget=budget), method="ai")
            elif request.operation == "emotions":
                payload = emotion_analysis(typed, budget=budget)
            else:
                payload = recheck_script(typed, budget=budget)
            budget.persist()
            result = StudioJobResult(
                operation=request.operation,
                base_hash=base_hash,
                payload=payload,
                calls=cp.get("calls", 0),
                duration_seconds=round(cp.get("elapsed", 0), 2),
            )
            cp["result"] = result.model_dump(mode="json")
            save_checkpoint(job_id, cp)
            self._update(
                job_id,
                progress=98,
                stage="保存创作结果",
                message="结果已写入检查点，正在提交任务终态。",
            )
            self._complete(job_id, result)
            metrics.record_task(
                "studio_" + request.operation,
                mode="ai",
                ok=True,
                duration_ms=int(result.duration_seconds * 1000),
                extra={"calls": result.calls},
            )
        except ValueError:
            # A typed response can still fail grounding/coverage validation.
            # Keep earlier valid steps, but let a retry replace this invalid one.
            key = getattr(budget, "last_response_key", None)
            if key:
                cp["responses"].pop(key, None)
                budget.persist()
            raise
        finally:
            client.client.close()

    def _complete_message(self, result):
        if result.operation == "recheck" and not result.payload.get("valid"):
            return "检查已完成，发现未解决问题；请查看结果后修正，当前工作稿未被后台覆盖。"
        return f"{LABELS[result.operation]}已完成，使用 {result.calls} 次调用。结果已保存。"


studio_jobs = StudioJobStore()
