"""Structured participant context; contains projections, never canonical WorldState."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from token_odyssey.inside_act.actions.query import WorldQuery
from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.entities import EntityKind, Item
from token_odyssey.inside_act.domain.knowledge import AgentRuntime, Observation
from token_odyssey.inside_act.domain.spatial import Placement, WorldState
from token_odyssey.inside_act.visibility import VisibilityService


class ScanObservationStatus(StrEnum):
    NEW = "new"
    MOVED = "moved"
    UNCHANGED = "unchanged"


class InteractionStatus(StrEnum):
    AVAILABLE = "available"
    CONTROLLED_BY_OTHER = "controlled_by_other"
    NOT_GUARANTEED = "not_guaranteed"


class EntityView(StrictModel):
    id: str
    kind: EntityKind
    name: str
    description: str
    placement: Placement | None = None
    observation_status: ScanObservationStatus | None = None
    interaction_status: InteractionStatus = InteractionStatus.NOT_GUARANTEED
    controller_id: str | None = None
    last_observed_round: int | None = Field(default=None, ge=0)


class EntityMemoryGroups(StrictModel):
    observed_this_turn: list[EntityView] = Field(default_factory=list)
    trusted_same_room: list[EntityView] = Field(default_factory=list)
    other_memories: list[EntityView] = Field(default_factory=list)


class EnvironmentProjection(StrictModel):
    full_observations: list[EntityView] = Field(default_factory=list)


class TurnContext(StrictModel):
    actor_id: str
    round_number: int = Field(ge=0)
    room_id: str
    room_name: str
    new_observations: list[Observation] = Field(default_factory=list)
    npcs: EntityMemoryGroups = Field(default_factory=EntityMemoryGroups)
    items: EntityMemoryGroups = Field(default_factory=EntityMemoryGroups)


class ContextProjector:
    def __init__(self) -> None:
        self.visibility = VisibilityService()

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
        observed_views = {
            view.id: view
            for view in environment.full_observations
            if view.observation_status in {
                ScanObservationStatus.NEW,
                ScanObservationStatus.MOVED,
            }
        }
        npc_groups = EntityMemoryGroups()
        item_groups = EntityMemoryGroups()
        query = WorldQuery(state)

        for entity_id, known in runtime.knowledge.entities.items():
            entity = state.entities.get(entity_id)
            if entity is None or entity_id == actor_id or entity.kind == EntityKind.ROOM:
                continue
            groups = npc_groups if entity.kind == EntityKind.CHARACTER else item_groups
            observed = observed_views.get(entity_id)
            if observed is not None:
                groups.observed_this_turn.append(
                    self._current_view(state, actor_id, observed, query)
                )
                continue

            placement_unchanged = known.last_observed_placement == state.placements.get(entity_id)
            same_room = state.root_room_of(entity_id) == room_id
            visibility = (
                self.visibility.item_visibility(state, actor_id, entity_id)
                if isinstance(entity, Item)
                else self.visibility.base_visibility(state, actor_id, entity_id)
            )
            if placement_unchanged and same_room and visibility > 0.0:
                groups.trusted_same_room.append(
                    self._current_view(
                        state,
                        actor_id,
                        EntityView(
                            id=entity_id,
                            kind=entity.kind,
                            name=entity.name,
                            description=entity.description,
                            placement=state.placements.get(entity_id),
                            last_observed_round=known.last_observed_round,
                        ),
                        query,
                    )
                )
            else:
                groups.other_memories.append(
                    EntityView(
                        id=entity_id,
                        kind=known.kind,
                        name=known.name,
                        description=known.description,
                        placement=known.last_observed_placement,
                        interaction_status=InteractionStatus.NOT_GUARANTEED,
                        last_observed_round=known.last_observed_round,
                    )
                )
        return TurnContext(
            actor_id=actor_id,
            round_number=round_number,
            room_id=room_id,
            room_name=state.room(room_id).name,
            new_observations=list(observations),
            npcs=npc_groups,
            items=item_groups,
        )

    @staticmethod
    def _current_view(
        state: WorldState,
        actor_id: str,
        view: EntityView,
        query: WorldQuery,
    ) -> EntityView:
        controller_id = state.controller_of(view.id)
        if view.kind == EntityKind.CHARACTER:
            available = state.root_room_of(view.id) == state.root_room_of(actor_id)
            interaction = (
                InteractionStatus.AVAILABLE
                if available
                else InteractionStatus.NOT_GUARANTEED
            )
        elif controller_id is not None and controller_id != actor_id:
            interaction = InteractionStatus.CONTROLLED_BY_OTHER
        elif query.is_accessible(actor_id, view.id):
            interaction = InteractionStatus.AVAILABLE
        else:
            interaction = InteractionStatus.NOT_GUARANTEED
        return view.model_copy(
            update={
                "interaction_status": interaction,
                "controller_id": controller_id,
            },
            deep=True,
        )
