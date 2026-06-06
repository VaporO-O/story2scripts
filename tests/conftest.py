import pytest


@pytest.fixture(autouse=True)
def disable_default_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY2SCRIPT_DISABLE_DOTENV", "1")
