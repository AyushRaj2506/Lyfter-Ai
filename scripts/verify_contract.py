"""Exercise the assignment contract against a running backend using HTTP."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def verify(base_url: str, origin: str | None) -> None:
    prefix = f"verify-{uuid4().hex}"

    def request(path, data=None, headers=None, method=None):
        headers = dict(headers or {})
        if data is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        req = Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=120) as response:
                body = response.read().decode()
                return response.status, json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body, response.headers
        except HTTPError as error:
            with error:
                return error.code, json.load(error), error.headers

    def send(suffix, message, expected_status=200, simulation=None):
        session_id = f"{prefix}-{suffix}"
        headers = {"X-Simulate-Failure": simulation} if simulation else {}
        if origin:
            headers["Origin"] = origin
        status, result, response_headers = request("/chat", {"session_id": session_id, "message": message}, headers)
        assert status == expected_status, f"POST /chat returned {status}, expected {expected_status}"
        if origin:
            assert response_headers.get("Access-Control-Allow-Origin") == origin, "CORS origin missing"
        if status != 200:
            assert "detail" in result, "Missing error detail"
            return result

        meta = result["metadata"]
        assert result["session_id"] == session_id and result["reply"].strip()
        assert meta["resolution_source"] in {"LIVE_INFERENCE", "LLM_CACHE"} and meta["model"]
        for field in ("latency_ms", "input_tokens", "output_tokens", "fallback_attempts"):
            assert type(meta[field]) is int and meta[field] >= 0, f"Invalid {field}"
        assert meta["latency_ms"] > 0
        print(f"  {suffix:16} {meta['resolution_source']:14} {meta['latency_ms']:5} ms  "
              f"tokens {meta['input_tokens']}/{meta['output_tokens']}  "
              f"key {meta['key_id']}  fallbacks {meta['fallback_attempts']}")
        return result

    status, health, _ = request("/health")
    assert status == 200 and health["status"] == "ok" and health["keys_configured"] >= 2 and health["cache_enabled"]
    print(f"PASS /health ({health['keys_configured']} keys, cache enabled)")

    if origin:
        status, _, headers = request("/chat", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Simulate-Failure",
        }, method="OPTIONS")
        assert status == 200 and headers.get("Access-Control-Allow-Origin") == origin
        assert "x-simulate-failure" in headers["Access-Control-Allow-Headers"].lower()
        print("PASS CORS preflight")

    prompt = f"What is the capital of France? Answer briefly. Reference {prefix}."
    first = send("first", prompt)
    cached = send("cached", prompt)
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert cached["metadata"]["resolution_source"] == "LLM_CACHE"
    assert first["reply"] == cached["reply"]
    assert cached["metadata"]["key_id"] is None and cached["metadata"]["fallback_attempts"] == 0
    assert cached["metadata"]["latency_ms"] < first["metadata"]["latency_ms"]
    for field in ("input_tokens", "output_tokens"):
        assert first["metadata"][field] == cached["metadata"][field] > 0
    other = send("different", prompt.replace("France", "Greece"))
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert abs(other["metadata"]["input_tokens"] - first["metadata"]["input_tokens"]) <= 4
    print("PASS exact cache hit, cache latency and stored per-request usage")

    for code in (429, 500):
        result = send(f"failover-{code}", prompt, simulation=f"key_1={code}")
        assert result["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert result["metadata"]["key_id"] == "key_2" and result["metadata"]["fallback_attempts"] == 1

    assert send("all-failed", prompt, 503, "*=429") == {"detail": "all providers unavailable"}
    send("invalid", " ", 422)
    for suffix in ("all-failed", "invalid"):
        status, result, _ = request(f"/sessions/{prefix}-{suffix}/messages")
        assert (status, result) == (404, {"detail": "session not found"})
    print("PASS simulated failover, 503 and 422 without history")

    code = f"ORION-{prefix[-8:].upper()}"
    initial = send("memory", f"Remember my project code {code}. Acknowledge briefly.")
    followup = send("memory", "What project code did I give you? Return just the code.")
    assert code in followup["reply"], "Model did not recall the code from the previous turn"
    status, history, _ = request(f"/sessions/{prefix}-memory/messages")
    assert status == 200 and history["session_id"] == f"{prefix}-memory"
    messages = history["messages"]
    assert [entry["role"] for entry in messages] == ["user", "assistant", "user", "assistant"]
    assert "metadata" not in messages[0] and "metadata" not in messages[2]
    assert messages[1]["metadata"] == initial["metadata"] and messages[3]["metadata"] == followup["metadata"]
    print("PASS multi-turn recall, oldest-first history and per-reply metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--origin", help="Check CORS for this frontend origin")
    args = parser.parse_args()
    try:
        verify(args.base_url, args.origin.rstrip("/") if args.origin else None)
    except (AssertionError, URLError, KeyError, ValueError) as error:
        parser.exit(1, f"Verification failed. {error}\n")


if __name__ == "__main__":
    main()
