"""Authored scheduling policy; independent of world rules and private prose."""

from typing import Annotated

from pydantic import Field, model_validator

from token_odyssey.common import FrozenModel

DEFAULT_FACT_WEIGHTS = {
    "voice": 0.2,
    "speaker": 0.3,
    "speech": 0.8,
    "handling": 0.25,
    "take": 1.0,
    "give": 1.2,
    "place": 0.8,
    "hide": 1.2,
    "show": 1.0,
    "item_location": 0.4,
    "search": 0.7,
    "inspect": 0.7,
    "discovery": 1.5,
    "open": 1.0,
    "close": 0.8,
    "lock": 1.2,
    "unlock": 1.2,
    "move": 1.2,
    "install": 1.4,
    "operate": 1.0,
    "arrival": 1.2,
    "departure": 0.8,
    "mechanism_seen": 1.8,
    "mechanism_heard": 1.2,
}
DEFAULT_DIRECT_WEIGHTS = {"say": 4.0, "show": 4.0, "give": 5.0}


class RoutingPolicy(FrozenModel):
    # Strategy names are resolved through runtime.router's factory registry so
    # an integration can add a policy without editing ActRunner.
    strategy: str = "interaction"
    actor_weights: dict[str, Annotated[float, Field(gt=0, le=100)]] = Field(default_factory=dict)
    allow_immediate_repeat: bool = False
    decay: float = Field(default=0.65, ge=0, lt=1)
    age_weight: float = Field(default=0.45, gt=0, le=2)
    attention_cap: float = Field(default=6, ge=1, le=12)
    fairness_rounds: int = Field(default=2, ge=1, le=4)
    idle_penalty: float = Field(default=0.55, ge=0, le=2)
    # Acts can tune interaction strength without changing global fact semantics.
    impulse_scale: float = Field(default=1, ge=0, le=1)
    # Extra interest in an *observed* object, not identity/location knowledge.
    interests: dict[str, dict[str, Annotated[float, Field(ge=0, le=2)]]] = Field(default_factory=dict)
    fact_weights: dict[str, Annotated[float, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: dict(DEFAULT_FACT_WEIGHTS)
    )
    direct_weights: dict[str, Annotated[float, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: dict(DEFAULT_DIRECT_WEIGHTS)
    )

    @model_validator(mode="before")
    @classmethod
    def merge_weight_overrides(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for field, defaults in (("fact_weights", DEFAULT_FACT_WEIGHTS), ("direct_weights", DEFAULT_DIRECT_WEIGHTS)):
            if field in data and isinstance(data[field], dict):
                data[field] = {**defaults, **data[field]}
        return data
