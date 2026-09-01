"""Structured participant context; contains projections, never canonical WorldState."""

from __future__ import annotations

from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.entities import EntityKind
from token_odyssey.inside_act.domain.knowledge import AgentRuntime, Observation
from token_odyssey.inside_act.domain.spatial import Placement, WorldState


class EntityView(StrictModel):
    id: str
    kind: EntityKind
    name: str
    description: str
    placement: Placement | None = None


class EnvironmentProjection(StrictModel):
    known_visible: list[EntityView] = Field(default_factory=list)
    newly_visible: list[EntityView] = Field(default_factory=list)


class TurnContext(StrictModel):
    actor_id: str
    round_number: int = Field(ge=0)
    room_id: str
    room_name: str
    new_observations: list[Observation] = Field(default_factory=list)
    known_visible: list[EntityView] = Field(default_factory=list)
    newly_visible: list[EntityView] = Field(default_factory=list)
    colocated_character_ids: list[str] = Field(default_factory=list)
    controlled_entity_ids: list[str] = Field(default_factory=list)
    available_room_ids: list[str] = Field(default_factory=list)


class ContextProjector:
    def build(
        self,
        state: WorldState,
        runtime: AgentRuntime,
        environment: EnvironmentProjection,
        round_number: int,
    ) -> TurnContext:
        actor_id = runtime.actor_id
        room_id = state.root_room_of(actor_id)
        observations = runtime.observations[runtime.observation_cursor :]
        runtime.observation_cursor = len(runtime.observations)
        visible_ids = {
            view.id for view in [*environment.known_visible, *environment.newly_visible]
        }
        colocated = [
            entity_id
            for entity_id in state.character_ids
            if entity_id != actor_id and entity_id in visible_ids
        ]
        controlled = [
            entity_id
            for entity_id in runtime.knowledge.entities
            if entity_id in state.entities and state.controller_of(entity_id) == actor_id
        ]
        return TurnContext(
            actor_id=actor_id,
            round_number=round_number,
            room_id=room_id,
            room_name=state.room(room_id).name,
            new_observations=list(observations),
            known_visible=environment.known_visible,
            newly_visible=environment.newly_visible,
            colocated_character_ids=colocated,
            controlled_entity_ids=controlled,
            available_room_ids=sorted(state.room_ids),
        )
