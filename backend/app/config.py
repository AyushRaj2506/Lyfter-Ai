import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

LOCAL_ORIGINS = ("http://localhost:3000", "http://localhost:5173")
PLACEHOLDER_KEYS = {"replace_with_first_api_key", "replace_with_second_api_key"}


def csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    model: str = "gemini/gemini-3.5-flash-lite"
    database_path: Path = Path("data/app.db")
    allowed_origins: tuple[str, ...] = LOCAL_ORIGINS
    allowed_origin_regex: str | None = None
    enable_failure_simulation: bool = False
    cache_type: Literal["local", "disk"] = "local"
    cache_ttl_seconds: int = 86400
    cache_dir: Path = Path("/data/litellm_cache")
    llm_timeout_seconds: int = 25
    llm_max_tokens: int = 2048
    max_history_messages: int = 40
    default_system_prompt: str = "You are a helpful, concise assistant."
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        sqlite_prefix = "sqlite:///"
        url = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
        path = url.removeprefix(sqlite_prefix)
        if not url.startswith(sqlite_prefix) or not path or path == ":memory:":
            raise ValueError("DATABASE_URL must be a file path starting with sqlite:///")

        cache_type = os.getenv("CACHE_TYPE", "local").strip().lower()
        if cache_type not in ("local", "disk"):
            raise ValueError("CACHE_TYPE must be local or disk")
        origins = csv(os.getenv("ALLOWED_ORIGINS", ",".join(LOCAL_ORIGINS)))
        keys = csv(os.getenv("LLM_API_KEYS", ""))
        return cls(
            api_keys=tuple(dict.fromkeys(key for key in keys if key not in PLACEHOLDER_KEYS)),
            model=os.getenv("LLM_MODEL", cls.model).strip(),
            database_path=Path(path),
            allowed_origins=tuple(origin.rstrip("/") for origin in origins),
            allowed_origin_regex=os.getenv("ALLOWED_ORIGIN_REGEX") or None,
            enable_failure_simulation=os.getenv("ENABLE_FAILURE_SIMULATION", "").strip().lower() == "true",
            cache_type=cache_type,
            cache_ttl_seconds=positive_int("CACHE_TTL_SECONDS", 86400),
            cache_dir=Path(os.getenv("CACHE_DIR", "/data/litellm_cache")),
            llm_timeout_seconds=positive_int("LLM_TIMEOUT_SECONDS", 25),
            llm_max_tokens=positive_int("LLM_MAX_TOKENS", 2048),
            max_history_messages=positive_int("MAX_HISTORY_MESSAGES", 40),
            default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", cls.default_system_prompt),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
