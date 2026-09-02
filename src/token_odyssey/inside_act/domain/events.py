"""Canonical action events and transaction results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SerializeAsAny, model_validator

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.spatial import Placement, WorldState


class ActionAmplitude(StrEnum):
    SUBTLE = "subtle"
    NORMAL = "normal"
    OVERT = "overt"

    @property
    def factor(self) -> float:
        return {
            ActionAmplitude.SUBTLE: 0.3,
            ActionAmplitude.NORMAL: 1.0,
            ActionAmplitude.OVERT: 2.0,
        }[self]


class EventSource(StrEnum):
    ACTION = "action"
    WORLD = "world"


class AnchorSnapshot(StrEnum):
    BEFORE = "before"
    AFTER = "after"


class VisibilityAnchor(StrictModel):
    entity_id: str
    snapshot: AnchorSnapshot = AnchorSnapshot.BEFORE


class ScanEnvironmentDirective(StrictModel):
    kind: Literal["scan_environment"] = "scan_environment"
    observer_id: str
    reason: str = ""


class KnowledgeGrantDirective(StrictModel):
    kind: Literal["grant_knowledge"] = "grant_knowledge"
    observer_id: str
    entity_ids: list[str] = Field(default_factory=list)
    text: str


ObservationDirective = Annotated[
    ScanEnvironmentDirective | KnowledgeGrantDirective,
    Field(discriminator="kind"),
]


class ActionEventData(StrictModel):
    """Base for action-owned, strictly typed canonical event payloads."""


class EmptyActionEventData(ActionEventData):
    pass


class WorldReactionEventData(ActionEventData):
    outcome: Literal["success", "failure"]


class WorldNoEffectEventData(ActionEventData):
    outcome: Literal["no_effect"] = "no_effect"


class WorldMechanicTrigger(StrictModel):
    kind: Literal["operate"] = "operate"
    target_entity_id: str = Field(min_length=1)


class ExecutionNotice(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    action_index: int | None = Field(default=None, ge=0)
    unexecuted_from_action_index: int | None = Field(default=None, ge=0)
    unexecuted_through_action_index: int | None = Field(default=None, ge=0)


class WorldEvent(StrictModel):
    sequence: int = Field(ge=1)
    round_number: int = Field(ge=1)
    frame_index: int = Field(ge=0)
    source: EventSource = EventSource.ACTION
    actor_id: str | None = None
    action_kind: str | None = None
    mechanic_id: str | None = None
    source_entity_id: str | None = None
    trigger_actor_id: str | None = None
    amplitude: ActionAmplitude = ActionAmplitude.NORMAL
    data: SerializeAsAny[ActionEventData] = Field(default_factory=EmptyActionEventData)
    intrinsic_visibility: float = Field(ge=0.0, le=1.0)
    anchors: list[VisibilityAnchor] = Field(default_factory=list)
    guaranteed_observer_ids: list[str] = Field(default_factory=list)
    knowledge_entity_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_fields(self) -> "WorldEvent":
        if self.source == EventSource.ACTION:
            if self.actor_id is None or self.action_kind is None:
                raise ValueError("action event requires actor_id and action_kind")
            if any(
                value is not None
                for value in (
                    self.mechanic_id,
                    self.source_entity_id,
                    self.trigger_actor_id,
                )
            ):
                raise ValueError("action event cannot carry WORLD mechanic fields")
        else:
            if self.actor_id is not None or self.action_kind is not None:
                raise ValueError("WORLD event cannot belong to an actor action")
            if self.source_entity_id is None or self.trigger_actor_id is None:
                raise ValueError(
                    "WORLD event requires source_entity_id and trigger_actor_id"
                )
            if isinstance(self.data, WorldNoEffectEventData):
                if self.mechanic_id is not None:
                    raise ValueError("no-effect WORLD event cannot carry mechanic_id")
            elif self.mechanic_id is None:
                raise ValueError("mechanic WORLD event requires mechanic_id")
        return self


class ValidationIssue(StrictModel):
    code: str
    message: str
    action_index: int | None = None


@dataclass(frozen=True)
class CommittedFrame:
    index: int
    before_state: WorldState
    after_state: WorldState
    events: tuple[WorldEvent, ...]
    directives: tuple[ObservationDirective, ...]


@dataclass(frozen=True)
class AcceptedTurn:
    committed_frames: tuple[CommittedFrame, ...]
    final_state: WorldState
    events: tuple[WorldEvent, ...]
    observation_directives: tuple[ObservationDirective, ...]
    execution_notices: tuple[ExecutionNotice, ...] = ()


@dataclass(frozen=True)
class RejectedTurn:
    validation_issues: tuple[ValidationIssue, ...]


Resolution = AcceptedTurn | RejectedTurn
