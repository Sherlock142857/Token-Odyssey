"""Normalized provider-neutral LLM request and response contract."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from token_odyssey.agents.contracts import ChatMessage, TokenUsage
from token_odyssey.inside_act.domain.common import StrictModel


class LLMProfile(StrictModel):
    backend_id: str
    model: str
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=900, ge=64, le=65536)
    extra: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(StrictModel):
    profile: LLMProfile
    messages: list[ChatMessage]
    json_object: bool = True


class LLMResponse(StrictModel):
    content: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str | None = None
    response_id: str | None = None


class LLMBackend(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...
