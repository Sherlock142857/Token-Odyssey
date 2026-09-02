"""Per-character subjective memory and projected observations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.entities import EntityKind
from token_odyssey.inside_act.domain.events import ExecutionNotice
from token_odyssey.inside_act.domain.spatial import Placement


class ObservationLevel(StrEnum):
    PARTIAL = "partial"
    FULL = "full"


class Observation(StrictModel):
    observer_id: str
    level: ObservationLevel
    text: str
    round_number: int = Field(ge=0)
    source_event_sequence: int | None = None
    is_system_update: bool = False


class KnownEntity(StrictModel):
    entity_id: str
    kind: EntityKind
    name: str
    description: str
    last_observed_placement: Placement | None
    first_observed_round: int = Field(ge=0)
    last_observed_round: int = Field(ge=0)
    currently_observable: bool = True


class AgentKnowledge(StrictModel):
    entities: dict[str, KnownEntity] = Field(default_factory=dict)


class AgentRuntime(StrictModel):
    actor_id: str
    knowledge: AgentKnowledge = Field(default_factory=AgentKnowledge)
    observations: list[Observation] = Field(default_factory=list)
    private_thoughts: list[str] = Field(default_factory=list)
    observation_cursor: int = Field(default=0, ge=0)
    last_validation_error: str | None = None
    execution_notices: list[ExecutionNotice] = Field(default_factory=list)
