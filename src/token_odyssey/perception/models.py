"""Participant-safe views and private perception records."""

from pydantic import Field

from token_odyssey.common import FrozenModel, Model
from token_odyssey.kernel.events import Fact, Issue
from token_odyssey.kernel.state import Placement


class EntityView(FrozenModel):
    id: str
    name: str
    kind: str
    description: str | None = None
    placement: Placement | None = None
    capabilities: tuple[str, ...] = ()
    is_open: bool | None = None
    basis: str = "scan"


class ExitView(FrozenModel):
    passage_id: str
    name: str
    destination_room_id: str
    destination_name: str
    is_open: bool
    allows_travel: bool


class Observation(FrozenModel):
    sequence: int
    observer_id: str
    world_revision: int
    source: str
    source_event_sequence: int | None = None
    facts: tuple[Fact, ...] = ()
    entities: tuple[EntityView, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)


class ActorView(FrozenModel):
    actor_id: str
    room_id: str
    room_name: str
    room_description: str
    exits: tuple[ExitView, ...] = ()
    inventory: tuple[EntityView, ...] = ()
    items: tuple[EntityView, ...] = ()
    characters: tuple[EntityView, ...] = ()
    observations: tuple[Observation, ...] = ()
    feedback: tuple[Issue, ...] = ()
    max_actions: int = 5
    continue_after_move: bool = False


class KnownEntity(Model):
    view: EntityView
    # Private comparison value. It must never appear in an ActorView.
    location_signature: tuple[tuple[str, str, str], ...] | None = None
    observed_revision: int = 0


class Memory(Model):
    known: dict[str, KnownEntity] = Field(default_factory=dict)
    inbox: list[Observation] = Field(default_factory=list)
    feedback: list[Issue] = Field(default_factory=list)
