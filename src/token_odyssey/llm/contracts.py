"""Provider-neutral transport types. No model metadata enters an action intent."""

from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from token_odyssey.common import FrozenModel


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(FrozenModel):
    role: ChatRole
    content: str


class TokenUsage(FrozenModel):
    prompt_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class LLMProfile(FrozenModel):
    backend_id: str
    model: str
    temperature: float = Field(default=0.9, ge=0, le=2)
    max_output_tokens: int = Field(default=1200, ge=64, le=65536)
    extra: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(FrozenModel):
    profile: LLMProfile
    messages: list[ChatMessage]
    json_object: bool = True


class LLMResponse(FrozenModel):
    content: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str | None = None
    response_id: str | None = None


class LLMExchange(FrozenModel):
    actor_id: str
    request_id: str
    request: LLMRequest
    response: LLMResponse | None = None
    error: str | None = None


class LLMBackend(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...
