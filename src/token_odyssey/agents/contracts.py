"""Uniform participant port used by LLM, scripted, replay, and future human adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field, model_validator

from token_odyssey.inside_act.actions.contracts import TurnPlan
from token_odyssey.inside_act.context import TurnContext
from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.events import ValidationIssue


class AgentError(RuntimeError):
    pass


class AgentUnavailableError(AgentError):
    """A backend failure that Act-level intent retries cannot repair."""


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(StrictModel):
    role: ChatRole
    content: str


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ValidationFeedback(StrictModel):
    issues: list[ValidationIssue] = Field(min_length=1)


class DecisionRequest(StrictModel):
    actor_id: str
    context: TurnContext | None = None
    feedback: ValidationFeedback | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> "DecisionRequest":
        if (self.context is None) == (self.feedback is None):
            raise ValueError("DecisionRequest requires exactly one of context or feedback")
        return self


class AgentDecision(StrictModel):
    actor_id: str
    raw_content: str
    plan: TurnPlan | None = None
    output_error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str | None = None
    response_id: str | None = None


class Participant(Protocol):
    def decide(self, request: DecisionRequest) -> AgentDecision: ...
