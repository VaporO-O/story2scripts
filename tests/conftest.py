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
