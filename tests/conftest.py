import pytest

from story2script.metrics import metrics


@pytest.fixture(autouse=True)
def disable_default_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_DISABLE_DOTENV", "1")


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    metrics.reset()
    yield
    metrics.reset()
