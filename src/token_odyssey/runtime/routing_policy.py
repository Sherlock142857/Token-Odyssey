"""Authored scheduling policy; independent of world rules and private prose."""

from typing import Annotated, Literal

from pydantic import Field

from token_odyssey.common import FrozenModel


class RoutingPolicy(FrozenModel):
    strategy: Literal["weighted", "shuffled"] = "weighted"
    decay: float = Field(default=0.65, ge=0, lt=1)
    age_weight: float = Field(default=0.45, gt=0, le=2)
    attention_cap: float = Field(default=6, ge=1, le=12)
    fairness_rounds: int = Field(default=2, ge=1, le=4)
    # Extra interest in an *observed* object, not identity/location knowledge.
    interests: dict[str, dict[str, Annotated[float, Field(ge=0, le=2)]]] = Field(default_factory=dict)
