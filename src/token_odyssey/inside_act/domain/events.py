"""Canonical action events and transaction results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SerializeAsAny

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


class WorldEvent(StrictModel):
    sequence: int = Field(ge=1)
    round_number: int = Field(ge=1)
    frame_index: int = Field(ge=0)
    actor_id: str
    action_kind: str
    amplitude: ActionAmplitude = ActionAmplitude.NORMAL
    data: SerializeAsAny[ActionEventData] = Field(default_factory=EmptyActionEventData)
    intrinsic_visibility: float = Field(ge=0.0, le=1.0)
    anchors: list[VisibilityAnchor] = Field(default_factory=list)
    guaranteed_observer_ids: list[str] = Field(default_factory=list)
    knowledge_entity_ids: list[str] = Field(default_factory=list)


class ValidationIssue(StrictModel):
    code: str
    message: str
    frame_index: int | None = None
    command_index: int | None = None


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


@dataclass(frozen=True)
class RejectedTurn:
    validation_issues: tuple[ValidationIssue, ...]


Resolution = AcceptedTurn | RejectedTurn
