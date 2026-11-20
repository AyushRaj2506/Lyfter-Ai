from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, StrictStr, StringConstraints, field_validator


def trim_message(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class ChatRequest(BaseModel):
    session_id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[
        StrictStr, StringConstraints(min_length=1, max_length=4000), BeforeValidator(trim_message)
    ]
    system_prompt: Annotated[StrictStr, StringConstraints(max_length=10000)] | None = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Session ID cannot be blank")
        return value

    @field_validator("system_prompt")
    @classmethod
    def normalize_system_prompt(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None


class Metadata(BaseModel):
    resolution_source: Literal["LLM_CACHE", "LIVE_INFERENCE"]
    model: str
    latency_ms: int = Field(ge=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    key_id: str | None
    fallback_attempts: int = Field(ge=0)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: Metadata


class UserMessage(BaseModel):
    role: Literal["user"]
    content: str


class AssistantMessage(BaseModel):
    role: Literal["assistant"]
    content: str
    metadata: Metadata


class SessionHistory(BaseModel):
    session_id: str
    messages: list[Annotated[UserMessage | AssistantMessage, Field(discriminator="role")]]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    keys_configured: int
    cache_enabled: bool
