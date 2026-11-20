import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.llm import litellm
from app.main import create_app
from app.simulation import parse_simulation

CHAT = {"session_id": "s-test", "message": "Hello there"}
HISTORY = "/sessions/s-test/messages"


def test_config_ignores_placeholders_and_normalizes_key_pool_and_origins(monkeypatch):
    monkeypatch.setenv("LLM_API_KEYS", " , first,replace_with_second_api_key,, second ")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://frontend.example/, , http://localhost:3000/")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "TrUe")
    config = Settings.from_env()
    assert config.api_keys == ("first", "second")
    assert config.allowed_origins == ("https://frontend.example", "http://localhost:3000")
    assert config.enable_failure_simulation


def test_health_and_public_routes(client):
    assert client.get("/health").json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/chat", "/sessions/{session_id}/messages"}
    assert paths["/chat"]["post"]["parameters"][0]["name"] == "X-Simulate-Failure"


def test_health_degrades_without_keys_or_database(settings, fake):
    with TestClient(create_app(replace(settings, api_keys=()), fake)) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "keys_configured": 0, "cache_enabled": True}

    assert settings.database_path.is_file()
    settings.database_path.write_bytes(b"not a database")
    with TestClient(create_app(settings, fake)) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "keys_configured": 2, "cache_enabled": True}


@pytest.mark.parametrize("body", [
    {}, {"session_id": "s-test"}, {"message": "hello"},
    {**CHAT, "message": ""}, {**CHAT, "message": " \t\n"},
    {**CHAT, "message": " " + "a" * 4001 + " "}, {**CHAT, "message": 24},
    {**CHAT, "session_id": ""}, {**CHAT, "session_id": " "}, {**CHAT, "session_id": 24},
    {**CHAT, "session_id": "s" * 129}, {**CHAT, "system_prompt": "a" * 10001}, [],
])
def test_invalid_input_does_not_touch_database(client, fake, body):
    response = client.post("/chat", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert fake.calls == []
    assert client.get(HISTORY).status_code == 404


@pytest.mark.parametrize("body", [b'{"session_id":', b'{"session_id": "\xff"}'])
def test_malformed_json_is_json_error(client, fake, body):
    response = client.post("/chat", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert fake.calls == []
    assert client.get(HISTORY).status_code == 404


def test_trim_before_validating_message_and_preserve_session_id(client, fake):
    session_id = " s/path "
    message = "𐀀" * 4000
    response = client.post("/chat", json={"session_id": session_id, "message": f"  {message}\n"})
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert fake.calls[0]["messages"][-1]["content"] == message
    history = client.get(f"/sessions/{quote(session_id, safe='')}/messages").json()
    assert history["messages"][0]["content"] == message


def test_chat_metadata_memory_and_isolation(client, fake, settings):
    first = client.post("/chat", json={**CHAT, "system_prompt": "Reply in short sentences."}).json()
    assert first["session_id"] == CHAT["session_id"]
    assert first["reply"] == "A test reply."
    metadata = first["metadata"]
    assert metadata == {
        "resolution_source": "LIVE_INFERENCE",
        "model": settings.model,
        "latency_ms": metadata["latency_ms"],
        "input_tokens": 6,
        "output_tokens": 3,
        "key_id": "key_1",
        "fallback_attempts": 0,
    }
    assert type(metadata["latency_ms"]) is int and metadata["latency_ms"] >= 1
    second = client.post("/chat", json={**CHAT, "message": "Next turn", "system_prompt": " "}).json()
    assert fake.calls[1]["messages"] == [
        {"role": "system", "content": "Reply in short sentences."},
        {"role": "user", "content": CHAT["message"]},
        {"role": "assistant", "content": first["reply"]},
        {"role": "user", "content": "Next turn"},
    ]
    assert second["metadata"]["input_tokens"] == 11
    assert second["metadata"]["output_tokens"] == 3

    history = client.get(HISTORY).json()
    assert history == {
        "session_id": "s-test",
        "messages": [
            {"role": "user", "content": CHAT["message"]},
            {"role": "assistant", "content": first["reply"], "metadata": metadata},
            {"role": "user", "content": "Next turn"},
            {"role": "assistant", "content": second["reply"], "metadata": second["metadata"]},
        ],
    }
    assert client.get("/sessions/other/messages").json() == {"detail": "session not found"}
    for session_id, same_size_message in (("other", "Blue gem"), ("third", "Rose bud")):
        result = client.post("/chat", json={"session_id": session_id, "message": same_size_message}).json()
        assert result["metadata"]["input_tokens"] == len(settings.default_system_prompt.split()) + 2
        assert len(fake.calls[-1]["messages"]) == 2


def test_database_persists_and_history_limit_only_affects_model(settings, fake):
    first = create_app(replace(settings, max_history_messages=2), fake)
    with TestClient(first) as client:
        assert client.post("/chat", json=CHAT).status_code == 200
        assert client.post("/chat", json={**CHAT, "message": "another message"}).status_code == 200

    with TestClient(create_app(replace(settings, max_history_messages=2), fake)) as client:
        assert len(client.get(HISTORY).json()["messages"]) == 4
        assert client.post("/chat", json={**CHAT, "message": "third message"}).status_code == 200
        assert [item["content"] for item in fake.calls[-1]["messages"][1:]] == [
            "another message", "A test reply.", "third message",
        ]
        assert len(client.get(HISTORY).json()["messages"]) == 6


def test_unwritable_sqlite_location_uses_temporary_storage(settings, fake, tmp_path):
    blocked = tmp_path / "file-in-the-way"
    blocked.touch()
    with TestClient(create_app(replace(settings, database_path=blocked / "app.db"), fake)) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/chat", json=CHAT).status_code == 200
        assert len(client.get(HISTORY).json()["messages"]) == 2
    assert blocked.is_file()


def test_failed_database_write_rolls_back_entire_turn(client, settings):
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript("""
            CREATE TRIGGER reject_assistant BEFORE INSERT ON messages
            WHEN NEW.role = 'assistant'
            BEGIN SELECT RAISE(ABORT, 'internal information'); END;
        """)
    failed = client.post("/chat", json=CHAT)
    assert failed.status_code == 500
    assert failed.json() == {"detail": "internal server error"}
    assert client.get(HISTORY).status_code == 404
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_same_session_requests_are_serialized(settings, fake):
    first_call = threading.Event()
    release_first = threading.Event()
    second_call = threading.Event()

    def completion(**kwargs):
        if kwargs["messages"][-1]["content"] == "first":
            first_call.set()
            assert release_first.wait(4)
        else:
            second_call.set()
        return fake(**kwargs)

    with TestClient(create_app(settings, completion)) as client, ThreadPoolExecutor(2) as executor:
        first = executor.submit(client.post, "/chat", json={**CHAT, "message": "first"})
        assert first_call.wait(4)
        second = executor.submit(client.post, "/chat", json={**CHAT, "message": "second"})
        try:
            assert not second_call.wait(0.15)
        finally:
            release_first.set()
        assert first.result().status_code == 200
        assert second.result().status_code == 200
    assert [item["content"] for item in fake.calls[-1]["messages"][1:]] == [
        "first", "A test reply.", "second",
    ]


@pytest.mark.parametrize("header", ["key_1=429", " key_1 = 500 ", "key_1=502", "key_1=503"])
def test_selected_key_fails_before_calling_provider(client, fake, settings, header):
    response = client.post("/chat", json=CHAT, headers={"X-Simulate-Failure": header})
    assert response.status_code == 200
    metadata = response.json()["metadata"]
    assert metadata["key_id"] == "key_2" and metadata["fallback_attempts"] == 1
    assert response.json()["reply"] == "A test reply."
    assert [call["api_key"] for call in fake.calls] == [settings.api_keys[1]]
    assert fake.calls[0]["cache"] == {"no-cache": True}
    assert fake.calls[0]["num_retries"] == 0
    assert client.post("/chat", json=CHAT).json()["metadata"]["key_id"] == "key_1"


@pytest.mark.parametrize("header", ["key_1=429,key_2=500", "*=429"])
def test_all_keys_failed_does_not_store_session(client, fake, settings, header):
    response = client.post("/chat", json=CHAT, headers={"X-Simulate-Failure": header})
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert fake.calls == []
    assert client.get(HISTORY).status_code == 404
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_all_keys_failed_does_not_update_existing_session(client, fake):
    client.post("/chat", json={**CHAT, "system_prompt": "Keep me"})
    before = client.get(HISTORY).json()
    assert client.post(
        "/chat", json={**CHAT, "system_prompt": "Discard me"}, headers={"X-Simulate-Failure": "*=429"}
    ).status_code == 503
    assert client.get(HISTORY).json() == before
    client.post("/chat", json=CHAT)
    assert fake.calls[-1]["messages"][0]["content"] == "Keep me"


def test_simulation_disabled_and_malformed_header(client, settings, fake):
    with TestClient(create_app(replace(settings, enable_failure_simulation=False), fake)) as disabled:
        result = disabled.post("/chat", json=CHAT, headers={"X-Simulate-Failure": "*=429"})
        assert result.json()["metadata"]["key_id"] == "key_1"
        assert fake.calls[-1]["cache"] == {}

    result = client.post("/chat", json=CHAT, headers={"X-Simulate-Failure": "nope,key_9=429,key_1=404,key_2=500=503"})
    assert result.json()["metadata"]["key_id"] == "key_1"
    assert fake.calls[-1]["cache"] == {}
    assert parse_simulation("key_2=500, *=429, unknown=503", 3) == {
        "key_1": 429, "key_2": 500, "key_3": 429,
    }


@pytest.mark.parametrize("error_class", [litellm.RateLimitError, litellm.InternalServerError, litellm.Timeout])
def test_litellm_provider_errors_fail_over_without_leaking_secrets(client, fake, settings, caplog, error_class):
    first_key, second_key = settings.api_keys
    fake.errors[first_key] = error_class(message=f"secret {first_key}", llm_provider="openai", model=settings.model)
    response = client.post("/chat", json=CHAT)
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1
    assert [call["api_key"] for call in fake.calls] == [first_key, second_key]

    fake.errors[second_key] = RuntimeError(second_key)
    failed = client.post("/chat", json=CHAT)
    assert failed.status_code == 503
    for key in settings.api_keys:
        for result in (response, failed, client.get(HISTORY)):
            assert key not in result.text
            assert key not in str(result.headers)
        assert key not in caplog.text


def test_empty_reply_counts_as_attempt(client, fake, settings):
    fake.blank_keys.add(settings.api_keys[0])
    assert client.post("/chat", json=CHAT).json()["metadata"]["key_id"] == "key_2"


def test_errors_and_cors(client, monkeypatch):
    origin = "http://localhost:3000"
    requested_headers = "Content-Type, X-Simulate-Failure"
    cors = client.options("/chat", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": requested_headers,
    })
    assert cors.status_code == 200
    assert cors.headers["access-control-allow-origin"] == origin
    assert cors.headers["access-control-allow-headers"] == requested_headers
    denied = client.options("/chat", headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"})
    assert denied.status_code == 400
    assert denied.json() == {"detail": "origin not allowed"}

    for response, status in ((client.get(HISTORY), 404), (client.get("/chat"), 405)):
        assert response.status_code == status
        assert "detail" in response.json()

    def crash(*_args, **_kwargs):
        raise RuntimeError("private failure string")

    monkeypatch.setattr(Database, "context", crash)
    failed = client.post("/chat", json=CHAT, headers={"Origin": origin})
    assert failed.status_code == 500
    assert failed.json() == {"detail": "internal server error"}
    assert failed.headers["access-control-allow-origin"] == origin
