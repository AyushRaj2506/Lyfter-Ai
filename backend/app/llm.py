import logging
import os
from dataclasses import dataclass
from importlib import import_module
from typing import Callable

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_TELEMETRY", "False")

import litellm
from litellm.caching import Cache

from .config import Settings
from .simulation import SimulatedProviderError

logger = logging.getLogger(__name__)
Completion = Callable[..., litellm.ModelResponse]


class ConversationCache(Cache):
    def get_cache_key(self, **kwargs: object) -> str:
        return super().get_cache_key(model=kwargs.get("model"), messages=kwargs.get("messages"))


def configure_cache(settings: Settings) -> Cache:
    # LiteLLM also writes its own diagnostics. The app logs sanitized attempt details below.
    for name in ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router", "httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).disabled = True
    litellm.suppress_debug_info = True
    litellm.turn_off_message_logging = True
    litellm.telemetry = False
    litellm.set_verbose = False
    # The pinned SDK imports this module lazily on its first synchronous cache hit.
    # Preload it at startup so the import does not inflate the user's cache-hit latency.
    import_module("litellm.llms.anthropic.pass_through.messages.response_cache")

    if settings.cache_type == "disk":
        try:
            return ConversationCache(
                type="disk", disk_cache_dir=str(settings.cache_dir), ttl=settings.cache_ttl_seconds
            )
        except Exception as error:
            logger.error("Disk cache initialization failed (%s). Using local cache.", type(error).__name__)

    return ConversationCache(
        type="local", default_in_memory_ttl=settings.cache_ttl_seconds, ttl=settings.cache_ttl_seconds
    )


class AllProvidersUnavailable(Exception):
    pass


class EmptyCompletion(Exception):
    pass


@dataclass(frozen=True)
class CompletionResult:
    reply: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int
    cache_hit: bool


def get_text(response: litellm.ModelResponse) -> str | None:
    if response.choices:
        message = getattr(response.choices[0], "message", None)
        text = getattr(message, "content", None)
        if isinstance(text, str) and text.strip():
            return text
    return None


class Gateway:
    def __init__(self, settings: Settings, completion: Completion | None = None):
        self.settings = settings
        self.completion = completion if completion is not None else litellm.completion

    def answer(self, messages: list[dict[str, str]], simulation: dict[str, int]) -> CompletionResult:
        for index, api_key in enumerate(self.settings.api_keys):
            key_id = f"key_{index + 1}"
            try:
                if key_id in simulation:
                    raise SimulatedProviderError(simulation[key_id])

                response = self.call(api_key, messages, bypass_cache=bool(simulation))
                cache_hit = response._hidden_params.get("cache_hit") is True
                text = get_text(response)
                if text is None and cache_hit:
                    response = self.call(api_key, messages, bypass_cache=True)
                    cache_hit = False
                    text = get_text(response)
                if text is None:
                    raise EmptyCompletion()

                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", None) or litellm.token_counter(
                    model=self.settings.model, messages=messages
                )
                output_tokens = getattr(usage, "completion_tokens", None) or litellm.token_counter(
                    model=self.settings.model, text=text, count_response_tokens=True
                )
                return CompletionResult(
                    reply=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    key_id=None if cache_hit else key_id,
                    fallback_attempts=0 if cache_hit else index,
                    cache_hit=cache_hit,
                )
            except Exception as error:
                status_code = getattr(error, "status_code", None)
                logger.warning(
                    "provider_attempt_failed key_id=%s error=%s status=%s",
                    key_id, type(error).__name__, status_code if isinstance(status_code, int) else "-",
                )
        raise AllProvidersUnavailable()

    def call(self, api_key: str, messages: list[dict[str, str]], bypass_cache: bool) -> litellm.ModelResponse:
        return self.completion(
            model=self.settings.model,
            messages=messages,
            api_key=api_key,
            num_retries=0,
            timeout=self.settings.llm_timeout_seconds,
            max_tokens=self.settings.llm_max_tokens,
            caching=True,
            cache={"no-cache": True} if bypass_cache else {},
        )
