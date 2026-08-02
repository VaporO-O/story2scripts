"""场景级流式：连续前缀水位、scene_cb 通道、SSE 端点与订阅者生命周期。"""

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from story2script.api_models import ConvertRequest
from story2script.conversion_jobs import ConversionJobStore
from story2script.converter import AIConverter, DemoConverter
from story2script.main import app
from story2script.parser import parse_chapters

client = TestClient(app)

NOVEL = (
    "第一章 开场\n林澈说：“出发吧。”\n"
    "第二章 线索\n林澈在码头等待。\n"
    "第三章 收束\n林澈回头看了一眼。"
)
NOVEL_CHAPTERS = parse_chapters(NOVEL)


def scene_dict(**overrides) -> dict:
    scene = {
        "id": "scene-1",
        "heading": "INT. 走廊 - NIGHT",
        "int_ext": "INT.",
        "time_of_day": "NIGHT",
        "location": "走廊",
        "source_chapter": "第一章 开场",
        "summary": "林澈察觉姐姐失踪并非意外。",
        "goal": "林澈试图确认真相。",
        "conflict": "新的线索推翻了意外结论。",
        "beat": "情节转折",
        "subtext": "林澈表面冷静，内心已经恐惧。",
        "characters": ["character-1"],
        "characters_present": ["character-1"],
        "props": [],
        "dramatization_decisions": [],
        "elements": [
            {"type": "action", "text": "林澈停下脚步，缓缓回头。"},
            {
                "type": "dialogue",
                "character": "character-1",
                "emotion": "紧张",
                "text": "不对……这不是意外。",
            },
        ],
        "camera_hints": ["近景：林澈绷紧的表情。"],
    }
    scene.update(overrides)
    return scene


def chapter_response(scenes: list[dict]) -> httpx.Response:
    content = json.dumps({"scenes": scenes}, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")


def chunk_title(prompt: str) -> str:
    return prompt.split("本章标题：")[1].split("\n")[0]


def ai_converter(handler) -> AIConverter:
    return AIConverter(client=httpx.Client(transport=httpx.MockTransport(handler)))


def demo_request() -> ConvertRequest:
    return ConvertRequest(novel_text=NOVEL, title="测试故事", genre="剧情", mode="demo")


def wait_for(store, job_id: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = store.snapshot(job_id)
        if snapshot.status in {"succeeded", "failed"}:
            return snapshot
        time.sleep(0.05)
    raise AssertionError("任务超时未完成")


# ---------------------------------------------------------------- 水位刷新


def test_scenes_flush_in_order_despite_out_of_order_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分块 4 路并发、完成顺序随机；水位保证按索引顺序 flush。"""
    configure_ai(monkeypatch)
    first_may_return = threading.Event()
    lock = threading.Lock()
    later_returns = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        title = chunk_title(prompt)
        if title.startswith("第一章"):
            # 让第一章最后返回：它是索引 0，会挡住后两章的显示（队头阻塞）。
            assert first_may_return.wait(timeout=10)
        else:
            with lock:
                later_returns["count"] += 1
                if later_returns["count"] >= 2:
                    first_may_return.set()
        return chapter_response([scene_dict(source_chapter=title)])

    streamed: list[dict] = []
    screenplay = ai_converter(handler).convert(
        NOVEL_CHAPTERS, scene_cb=streamed.append
    )

    # 第二、三章先完成，但流出来的顺序仍是章节顺序。
    # 不能用 sorted() 判序：中文数字按码位排是"一三二"（三 U+4E09 < 二 U+4E8C），
    # 要按章节在原文里的位置比。
    chapters = [scene["source_chapter"] for scene in streamed]
    document_order = [chapter.title for chapter in NOVEL_CHAPTERS]
    positions = [document_order.index(title) for title in chapters]
    assert positions == sorted(positions)
    # 队头阻塞的正面效果：第一章最后返回，却仍然第一个显示
    assert chapters[0].startswith("第一章")
    # 流式顺序与最终剧本一致
    assert chapters == [scene.source_chapter for scene in screenplay.scenes]


def test_scene_ids_are_final_when_streamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """编号在 flush 时定终值，取代收尾的全局重排：流出去的 id 不会再变。"""
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        title = chunk_title(prompt)
        # 每个分块都自称 scene-1：合并后必须重新编号
        return chapter_response([scene_dict(source_chapter=title)])

    streamed: list[dict] = []
    screenplay = ai_converter(handler).convert(NOVEL_CHAPTERS, scene_cb=streamed.append)

    streamed_ids = [scene["id"] for scene in streamed]
    assert streamed_ids == [f"scene-{index}" for index in range(1, len(streamed) + 1)]
    # 关键断言：流式期间给出的 id 与最终剧本里的 id 完全一致
    assert streamed_ids == [scene.id for scene in screenplay.scenes]


def test_failed_chunk_does_not_block_later_scenes(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败的分块要跳过而不是永久挡住水位——否则一个失败片段会吞掉整篇流式。"""
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        title = chunk_title(prompt)
        if title.startswith("第一章"):
            return httpx.Response(500)
        return chapter_response([scene_dict(source_chapter=title)])

    streamed: list[dict] = []
    screenplay = ai_converter(handler).convert(NOVEL_CHAPTERS, scene_cb=streamed.append)

    assert streamed, "第一章失败不应吞掉后两章的流式输出"
    assert all(not scene["source_chapter"].startswith("第一章") for scene in streamed)
    assert [scene["id"] for scene in streamed] == [scene.id for scene in screenplay.scenes]


def test_scene_cb_and_progress_cb_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """两条通道互不干扰：progress_cb 的 done 单调、total 唯一，不受 scene_cb 影响。"""
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        return chapter_response([scene_dict(source_chapter=chunk_title(prompt))])

    events: list[tuple[int, int, str]] = []
    streamed: list[dict] = []
    ai_converter(handler).convert(
        NOVEL_CHAPTERS,
        progress_cb=lambda done, total, note: events.append((done, total, note)),
        scene_cb=streamed.append,
    )

    dones = [done for done, _, _ in events]
    assert dones == sorted(dones)
    assert len({total for _, total, _ in events}) == 1
    assert streamed


def test_meta_arrives_before_first_scene(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式场景里的说话人是 character id，没有名册前端只能显示 character-1。"""
    configure_ai(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content.decode("utf-8"))["messages"][0]["content"]
        if "本章片段原文" not in prompt:
            return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})
        return chapter_response([scene_dict(source_chapter=chunk_title(prompt))])

    order: list[str] = []
    metas: list[dict] = []

    def meta_cb(meta: dict) -> None:
        order.append("meta")
        metas.append(meta)

    ai_converter(handler).convert(
        NOVEL_CHAPTERS,
        title="测试故事",
        meta_cb=meta_cb,
        scene_cb=lambda scene: order.append("scene"),
    )

    assert order[0] == "meta"
    assert order.count("meta") == 1
    assert metas[0]["title"] == "测试故事"
    assert any(item["id"] == "character-1" for item in metas[0]["characters"])


def test_demo_converter_streams_scenes_too() -> None:
    """两条实现的回调行为保持对称：只接参数不上报会让调用方无法分辨"没有流式"。"""
    streamed: list[dict] = []
    metas: list[dict] = []
    screenplay = DemoConverter().convert(
        NOVEL_CHAPTERS, title="测试故事", scene_cb=streamed.append, meta_cb=metas.append
    )

    assert len(streamed) == len(screenplay.scenes)
    assert [scene["id"] for scene in streamed] == [scene.id for scene in screenplay.scenes]
    assert metas and metas[0]["title"] == "测试故事"


# ---------------------------------------------------------------- 订阅者


def test_subscriber_receives_backlog_and_live_events() -> None:
    store = ConversionJobStore()
    store.publish("job-x", {"type": "scene", "index": 1})
    queue, backlog = store.subscribe("job-x")

    # 订阅之前的事件由补发拿到：重连不丢场景
    assert backlog == [{"type": "scene", "index": 1}]

    store.publish("job-x", {"type": "scene", "index": 2})
    assert queue.get_nowait() == {"type": "scene", "index": 2}
    # 补发的事件不会重复进队列
    assert queue.empty()

    store.unsubscribe("job-x", queue)


def test_unsubscribe_removes_queue() -> None:
    """关掉的标签页不能留下一个持续增长的队列。"""
    store = ConversionJobStore()
    queue, _ = store.subscribe("job-y")
    assert store._subscribers.get("job-y")

    store.unsubscribe("job-y", queue)
    assert "job-y" not in store._subscribers

    # 退订后不再收到事件
    store.publish("job-y", {"type": "scene", "index": 1})
    assert queue.empty()


def test_progress_and_done_events_are_published() -> None:
    store = ConversionJobStore()
    job_id = store.create(demo_request()).job_id
    queue, backlog = store.subscribe(job_id)
    wait_for(store, job_id)

    events = list(backlog)
    while not queue.empty():
        events.append(queue.get_nowait())

    kinds = [event["type"] for event in events]
    assert "scene" in kinds
    assert "progress" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["status"] == "succeeded"
    store.unsubscribe(job_id, queue)


# ---------------------------------------------------------------- SSE 端点


def read_sse(job_id: str) -> list[dict]:
    events: list[dict] = []
    with client.stream("GET", f"/api/convert/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # 反向代理缓冲会把事件攒成一批，流式效果消失
        assert response.headers["x-accel-buffering"] == "no"
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event["type"] == "done":
                break
    return events


def test_sse_streams_scenes_then_terminates() -> None:
    job_id = client.post(
        "/api/convert/jobs",
        json={"novel_text": NOVEL, "title": "测试故事", "genre": "剧情", "mode": "demo"},
    ).json()["job_id"]

    events = read_sse(job_id)
    kinds = [event["type"] for event in events]

    # 名册必须先于第一个场景到达（前端靠它把 character-1 显示成人名）。
    # 但它不是流里的第一条：任务在调用转换器之前就已汇报过安全检查、解析章节。
    assert kinds.count("meta") == 1
    assert kinds.index("meta") < kinds.index("scene")
    assert kinds[-1] == "done"
    # 场景编号连续，且流式期间就是终值
    indexes = [event["index"] for event in events if event["type"] == "scene"]
    assert indexes == list(range(1, len(indexes) + 1))

    # 结果本身不在事件流里（可能是一整篇剧本），仍由 snapshot 取回
    snapshot = client.get(f"/api/convert/jobs/{job_id}").json()
    assert snapshot["status"] == "succeeded"
    assert len(snapshot["result"]["screenplay"]["scenes"]) == len(indexes)


def test_sse_rejects_unknown_job() -> None:
    assert client.get("/api/convert/jobs/does-not-exist/events").status_code == 404


def test_sse_emits_done_for_already_finished_job() -> None:
    """缓存命中会瞬间完成、零流式：订阅时任务可能已结束，也要正常收尾。"""
    job_id = client.post(
        "/api/convert/jobs",
        json={"novel_text": NOVEL, "title": "测试故事", "genre": "剧情", "mode": "demo"},
    ).json()["job_id"]

    # 先等任务跑完，再订阅——模拟"什么都没流就结束了"
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/convert/jobs/{job_id}").json()["status"] in {
            "succeeded",
            "failed",
        }:
            break
        time.sleep(0.05)

    events = read_sse(job_id)
    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "succeeded"


def test_sse_cleans_up_subscriber_after_stream_ends() -> None:
    from story2script.conversion_jobs import conversion_jobs

    job_id = client.post(
        "/api/convert/jobs",
        json={"novel_text": NOVEL, "title": "测试故事", "genre": "剧情", "mode": "demo"},
    ).json()["job_id"]

    read_sse(job_id)

    # 流结束后订阅者必须被移除，镜像也随之释放
    assert job_id not in conversion_jobs._subscribers
    assert job_id not in conversion_jobs._events
