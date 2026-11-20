"""Probe LiteLLM directly. Pass --live to use the configured two-key pool."""

import argparse
import sys
from importlib.metadata import version
from time import perf_counter
from uuid import uuid4

from app.config import Settings
from app.llm import configure_cache, litellm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    options = parser.parse_args()
    settings = Settings.from_env()
    if options.live and len(settings.api_keys) < 2:
        parser.error("Set LLM_API_KEYS to at least two real provider keys to run the live probe")

    model = settings.model
    keys = settings.api_keys if options.live else ("test-only-first", "test-only-second")
    messages = [
        {"role": "system", "content": settings.default_system_prompt},
        {"role": "user", "content": f"Reply with one greeting. Verification reference {uuid4().hex}."},
    ]
    litellm.cache = configure_cache(Settings(cache_ttl_seconds=60))
    print(f"LiteLLM {version('litellm')} using {'live provider' if options.live else 'mock_response'}")

    def call(key: str, **extra):
        return litellm.completion(
            model=model, messages=messages, api_key=key,
            caching=True, num_retries=0, timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens, **extra,
        )

    mock = {} if options.live else {"mock_response": "Hello from the first completion.", "mock_delay": 0.15}
    started = perf_counter()
    first = call(keys[0], **mock)
    live_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    # No mock response on the hit. A miss would attempt a provider call.
    second = call(keys[1])
    cache_ms = (perf_counter() - started) * 1000
    assert not first._hidden_params.get("cache_hit")
    assert second._hidden_params.get("cache_hit") is True
    assert first.choices[0].message.content == second.choices[0].message.content
    assert first.usage == second.usage
    bypass_mock = {} if options.live else {"mock_response": "Second live completion."}
    fresh = call(keys[1], cache={"no-cache": True}, **bypass_mock)
    assert not fresh._hidden_params.get("cache_hit")
    assert fresh.choices[0].message.content.strip()
    print(f"Cross-key cache hit kept reply and usage ({live_ms:.1f} ms miss, {cache_ms:.1f} ms hit)")
    print("Bypass reached the second key")
    for error_type in (litellm.RateLimitError, litellm.InternalServerError, litellm.Timeout):
        print(f"Provider exception: {error_type.__name__}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Probe failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
