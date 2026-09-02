"""Action extension contract. Each action owns validation, effects, rendering, and prompt metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field, SerializeAsAny

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.events import (
    ActionAmplitude,
    ActionEventData,
    EmptyActionEventData,
    ObservationDirective,
    VisibilityAnchor,
    WorldEvent,
)
from token_odyssey.inside_act.domain.spatial import Placement, WorldState


MAX_ACTIONS_PER_TURN = 5


class BaseActionIntent(StrictModel):
    kind: str = Field(min_length=1)
    amplitude: ActionAmplitude = ActionAmplitude.NORMAL


class ActionFrame(StrictModel):
    commands: list[SerializeAsAny[BaseActionIntent]] = Field(
        min_length=1, max_length=MAX_ACTIONS_PER_TURN
    )


class TurnPlan(StrictModel):
    private_thought: str = ""
    frames: list[ActionFrame] = Field(min_length=1, max_length=MAX_ACTIONS_PER_TURN)


@dataclass(frozen=True)
class PlacementMutation:
    entity_id: str
    before: Placement
    after: Placement

    def apply(self, state: WorldState) -> None:
        current = state.placements.get(self.entity_id)
        if current != self.before:
            raise RuntimeError(
                f"placement changed after validation for {self.entity_id!r}: "
                f"expected {self.before!r}, got {current!r}"
            )
        state.placements[self.entity_id] = self.after


class WorldMutation(Protocol):
    """Action-local mutations can extend world behavior without editing the Harness."""

    def apply(self, state: WorldState) -> None: ...


@dataclass
class ActionEffect:
    data: ActionEventData = field(default_factory=EmptyActionEventData)
    mutations: list[WorldMutation] = field(default_factory=list)
    anchors: list[VisibilityAnchor] = field(default_factory=list)
    guaranteed_observer_ids: list[str] = field(default_factory=list)
    knowledge_entity_ids: list[str] = field(default_factory=list)
    directives: list[ObservationDirective] = field(default_factory=list)
    emit_event: bool = True

    def __post_init__(self) -> None:
        if not self.emit_event and any(
            (
                self.mutations,
                self.anchors,
                self.guaranteed_observer_ids,
                self.knowledge_entity_ids,
                self.directives,
            )
        ):
            raise ValueError("A silent ActionEffect cannot mutate state or project observations")


@dataclass(frozen=True)
class ActionContext:
    actor_id: str
    state: WorldState
    query: "WorldQueryProtocol"


class WorldQueryProtocol(Protocol):
    state: WorldState


ValidateAction = Callable[[ActionContext, BaseActionIntent], list[str]]
PlanAction = Callable[[ActionContext, BaseActionIntent], ActionEffect]
RenderAction = Callable[[WorldState, WorldEvent], str]
KnownReferences = Callable[[BaseActionIntent], set[str]]


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    intent_model: type[BaseActionIntent]
    event_model: type[ActionEventData]
    validate: ValidateAction
    plan: PlanAction
    known_reference_extractor: KnownReferences
    intrinsic_visibility: float
    render_full: RenderAction
    render_partial: RenderAction
    prompt_usage: str
    prompt_requirements: tuple[str, ...] = ()
    prompt_effect: str = ""
    prompt_misuses: tuple[str, ...] = ()
    is_move_checkpoint: bool = False
    stale_after_move_recoverable: bool = False

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("ActionSpec kind cannot be empty")
        if not 0.0 <= self.intrinsic_visibility <= 1.0:
            raise ValueError("ActionSpec intrinsic_visibility must be between zero and one")
