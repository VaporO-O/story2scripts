from time import perf_counter

from .api_models import ConvertJobStatusResponse
from .api_models import ConvertRequest
from .api_models import ConvertResponse
from .converter import get_converter
from .job_store import DurableJobStore
from .metrics import metrics
from .parser import parse_chapters
from .scene_review import review_and_improve
from .security import screen_novel_text
from .yaml_export import screenplay_to_yaml


class ConversionJobStore(DurableJobStore):
    """转换任务队列：持久化、可恢复；业务管线保持不变。"""

    kind = "convert"
    request_model = ConvertRequest
    result_model = ConvertResponse
    response_model = ConvertJobStatusResponse
    queued_stage = "等待转换"
    queued_message = "转换任务已创建，等待后端开始处理。"
    complete_stage = "转换完成"
    fail_stage = "转换失败"

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
            self._update(job_id, status="running", progress=5, stage="安全检查")
            security_warnings = screen_novel_text(request.novel_text)

            self._update(job_id, progress=10, stage="解析章节")
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

            # 转换是整条链路里最慢的一段（AI 模式下藏着建索引、逐段检索、
            # 分块调用与人物小传共约 20 次网络往返），把它的内部进度铺开到
            # 45→68（开启机审时给后续阶段留出区间）或 45→88。
            span = 23 if request.enable_review else 43

            def progress_cb(done: int, total: int, note: str) -> None:
                progress = 45 + int(span * done / max(1, total))
                self._update(job_id, progress=progress, stage="生成剧本", message=note)

            scene_count = 0

            def meta_cb(meta: dict) -> None:
                # 先于第一个场景到达：流式场景里的说话人是 character id，
                # 前端要靠这份名册才能显示人名而不是 character-1。
                self.publish(job_id, {"type": "meta", "meta": meta})

            def scene_cb(scene: dict) -> None:
                # 场景定稿即推送，让用户看到剧本逐场长出来。编号在转换器的水位
                # 刷新时就已定终值，所以这里推出去的 id 不会再变动。
                nonlocal scene_count
                scene_count += 1
                self.publish(job_id, {"type": "scene", "index": scene_count, "scene": scene})

            screenplay = converter.convert(
                chapters=chapters,
                title=request.title,
                genre=request.genre,
                adaptation_type=request.adaptation_type,
                progress_cb=progress_cb,
                scene_cb=scene_cb,
                meta_cb=meta_cb,
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
                security_warnings=security_warnings,
                conversion_warnings=list(getattr(converter, "last_run_warnings", [])),
            )
            self._complete(job_id, result)
            record(ok=True, scene_count=len(result.screenplay.scenes))
        except ValueError as exc:
            self._fail(job_id, str(exc))
            record(ok=False, error=str(exc))
        except Exception as exc:
            self._fail(job_id, f"转换任务失败：{exc}")
            record(ok=False, error=f"转换任务失败：{exc}")

    def _complete_message(self, result: ConvertResponse) -> str:
        return f"已生成 {len(result.screenplay.scenes)} 个场景。"


def _mode_name(mode: str) -> str:
    return "AI" if mode == "ai" else "本地"


def _generation_message(mode: str) -> str:
    if mode == "ai":
        return "正在调用 AI 生成 Screenplay JSON，并等待后端 Schema 校验。"
    return "正在使用本地规则拆分场景、生成人物和戏剧要素。"


conversion_jobs = ConversionJobStore()
