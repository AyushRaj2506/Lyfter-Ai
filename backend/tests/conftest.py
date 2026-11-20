from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import litellm
from app.main import create_app


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.errors = {}
        self.blank_keys = set()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        api_key = kwargs["api_key"]
        if api_key in self.errors:
            raise self.errors[api_key]
        messages = kwargs["messages"]
        input_tokens = sum(len(entry["content"].split()) for entry in messages)
        content = " " if api_key in self.blank_keys else "A test reply."
        return litellm.ModelResponse(
            model=kwargs["model"],
            choices=[{"message": {"role": "assistant", "content": content}}],
            usage={"prompt_tokens": input_tokens, "completion_tokens": 3, "total_tokens": input_tokens + 3},
        )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        api_keys=("private-test-value-alpha", "private-test-value-beta"),
        model="gpt-4o-mini",
        database_path=tmp_path / "nested" / "test.db",
        enable_failure_simulation=True,
    )


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(settings, fake) -> Iterator[TestClient]:
    with TestClient(create_app(settings, fake)) as test_client:
        yield test_client
