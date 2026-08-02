import pytest

from story2script.llm_cache import llm_cache
from story2script.metrics import metrics


@pytest.fixture(autouse=True)
def disable_default_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_DISABLE_DOTENV", "1")


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture(autouse=True)
def reset_llm_cache() -> None:
    llm_cache.clear()
    yield
    llm_cache.clear()


@pytest.fixture(autouse=True)
def isolate_jobs_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 任务库路径按调用惰性解析，import 期创建的单例也会落到本测试的 tmp 目录。
    monkeypatch.setenv("STORY2SCRIPT_JOBS_DB", str(tmp_path / "jobs.db"))


@pytest.fixture(autouse=True)
def sandbox_file_roots(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 文件沙箱指向本测试的 tmp 目录：既有 tmp_path 读写测试不受影响，
    # 越界测试用 tmp_path 之外的路径断言拒绝。
    monkeypatch.setenv("STORY2SCRIPT_FILE_ROOTS", str(tmp_path))


@pytest.fixture(autouse=True)
def no_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORY2SCRIPT_API_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认退避是 1s+3s，会让每个走重试路径的测试白等 4 秒。需要验证退避本身的
    # 测试自己覆盖这个环境变量。
    monkeypatch.setenv("AI_RETRY_BACKOFF_SECONDS", "0")
