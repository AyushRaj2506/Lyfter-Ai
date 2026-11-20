from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app

CHAT = {"session_id": "retry-session", "message": "Explain caching.", "system_prompt": "Be brief."}
HEADERS = {"Idempotency-Key": "request-one"}


def test_same_request_reuses_the_saved_reply_across_restarts(settings, fake):
    with TestClient(create_app(settings, fake)) as client, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/chat", json=CHAT, headers=HEADERS)
        retry = pool.submit(client.post, "/chat", json=CHAT, headers=HEADERS)
        assert first.result().status_code == retry.result().status_code == 200
        original = first.result().json()
        assert retry.result().json() == original
        assert len(fake.calls) == 1

    with TestClient(create_app(settings, fake)) as client:
        history_url = "/sessions/retry-session/messages"
        assert len(client.get(history_url).json()["messages"]) == 2
        assert client.post("/chat", json=CHAT, headers=HEADERS).json() == original
        assert len(fake.calls) == 1
        assert client.post("/chat", json=CHAT, headers={"Idempotency-Key": "request-two"}).status_code == 200
        assert len(client.get(history_url).json()["messages"]) == 4
        assert client.post("/chat", json=CHAT | {"session_id": "other-session"}, headers=HEADERS).status_code == 200
        assert len(fake.calls) == 3


def test_rejects_reusing_a_request_id_for_a_different_turn(client, fake):
    assert client.post("/chat", json=CHAT, headers=HEADERS).status_code == 200
    for data, headers in [
        (CHAT | {"message": "Different message."}, HEADERS),
        (CHAT | {"system_prompt": "Use a table."}, HEADERS),
        (CHAT, HEADERS | {"X-Simulate-Failure": "key_1=429"}),
    ]:
        response = client.post("/chat", json=data, headers=headers)
        assert response.status_code == 409
        assert response.json() == {"detail": "idempotency key already used for another request"}
    assert len(fake.calls) == 1


def test_provider_failure_does_not_reserve_request_id(client, fake):
    fake.blank_keys.update(("private-test-value-alpha", "private-test-value-beta"))
    assert client.post("/chat", json=CHAT, headers=HEADERS).status_code == 503
    assert client.get("/sessions/retry-session/messages").status_code == 404
    fake.blank_keys.clear()

    assert client.post("/chat", json=CHAT, headers=HEADERS).status_code == 200
    assert len(client.get("/sessions/retry-session/messages").json()["messages"]) == 2
