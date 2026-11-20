from dataclasses import replace

from fastapi.testclient import TestClient

from app.llm import Gateway, litellm
from app.main import create_app


def mock_completion(**kwargs):
    # Delay applies only when LiteLLM actually invokes the mock provider.
    return litellm.completion(**kwargs, mock_response="Paris.", mock_delay=0.15)


def test_litellm_cache_hit_cross_key_and_session(settings):
    provider_calls = []

    def completion(**kwargs):
        provider_calls.append(kwargs["api_key"])
        return mock_completion(**kwargs)

    with TestClient(create_app(settings, completion)) as client:
        def send(session_id, message="What is the capital of France?", simulation=None, **extra):
            headers = {"X-Simulate-Failure": simulation} if simulation else {}
            return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers)

        live = send("live", simulation="key_1=429").json()
        hit = send("cached").json()
        assert provider_calls[:2] == [settings.api_keys[1], settings.api_keys[0]]
        assert live["reply"] == hit["reply"] == "Paris."
        assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert live["metadata"]["key_id"] == "key_2"
        assert live["metadata"]["fallback_attempts"] == 1
        cached_metadata = hit["metadata"]
        assert cached_metadata["resolution_source"] == "LLM_CACHE"
        assert cached_metadata["key_id"] is None
        assert cached_metadata["fallback_attempts"] == 0
        assert cached_metadata["latency_ms"] < live["metadata"]["latency_ms"]
        for field in ("input_tokens", "output_tokens"):
            assert cached_metadata[field] == live["metadata"][field] > 0
        assert client.get("/sessions/cached/messages").json()["messages"][-1]["metadata"] == cached_metadata

        assert send("different", "What is the capital of Spain?").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert send("cached").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert send("changed-prompt", system_prompt="Speak like a pirate.").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert send("disabled-first-again", simulation="key_1=429").json()["metadata"]["key_id"] == "key_2"
        assert send("failed", simulation="*=429").status_code == 503
        assert client.get("/sessions/failed/messages").status_code == 404


def test_disk_cache_persists_between_lifespans_and_partitions_by_model(settings, tmp_path):
    settings = replace(settings, cache_type="disk", cache_dir=tmp_path / "cache")
    for session_id, model, source in (
        ("one", "gpt-4o-mini", "LIVE_INFERENCE"),
        ("two", "gpt-4o-mini", "LLM_CACHE"),
        ("three", "gpt-4o", "LIVE_INFERENCE"),
    ):
        with TestClient(create_app(replace(settings, model=model), mock_completion)) as client:
            result = client.post("/chat", json={"session_id": session_id, "message": "Say hello."})
            assert result.status_code == 200
            assert result.json()["metadata"]["resolution_source"] == source


def test_empty_cache_hit_retries_same_key_and_estimates_missing_usage(settings, monkeypatch):
    calls = []
    counts = []

    def completion(**kwargs):
        calls.append(kwargs)
        hit = len(calls) == 1
        response = litellm.ModelResponse(
            choices=[{"message": {"role": "assistant", "content": " " if hit else "Good day!"}}],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        response._hidden_params["cache_hit"] = hit
        return response

    def token_counter(**kwargs):
        counts.append(kwargs)
        return 4

    monkeypatch.setattr(litellm, "token_counter", token_counter)
    messages = [{"role": "user", "content": "Hello!"}]
    result = Gateway(settings, completion).answer(messages, {})
    assert [call["api_key"] for call in calls] == [settings.api_keys[0], settings.api_keys[0]]
    assert calls[-1]["cache"] == {"no-cache": True}
    assert result.reply == "Good day!" and not result.cache_hit
    assert result.fallback_attempts == 0 and result.key_id == "key_1"
    assert result.input_tokens == result.output_tokens == 4
    assert counts == [
        {"model": settings.model, "messages": messages},
        {"model": settings.model, "text": "Good day!", "count_response_tokens": True},
    ]
