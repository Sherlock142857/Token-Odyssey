"""Structured participant context; contains projections, never canonical WorldState."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.entities import EntityKind, Item
from token_odyssey.inside_act.domain.events import ExecutionNotice
from token_odyssey.inside_act.domain.knowledge import AgentRuntime
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState
from token_odyssey.inside_act.visibility import VisibilityService


class ScanObservationStatus(StrEnum):
    NEW = "new"
    MOVED = "moved"
    UNCHANGED = "unchanged"


class EntityView(StrictModel):
    id: str
    name: str
    placement: Placement | None = None
    description: str | None = None
    change: ScanObservationStatus | None = None


class EntityMemoryGroups(StrictModel):
    new_or_changed: list[EntityView] = Field(default_factory=list)
    visible_same_location: list[EntityView] = Field(default_factory=list)
    memories: list[EntityView] = Field(default_factory=list)


class InventoryView(StrictModel):
    attached: list[EntityView] = Field(default_factory=list)
    inside: list[EntityView] = Field(default_factory=list)


class LocationView(StrictModel):
    id: str
    name: str


class EnvironmentProjection(StrictModel):
    full_observations: list[EntityView] = Field(default_factory=list)


class TurnContext(StrictModel):
    actor_id: str
    location: LocationView
    observations_since_last_action: list[str] = Field(default_factory=list)
    last_action_feedback: list[ExecutionNotice] = Field(default_factory=list)
    characters: EntityMemoryGroups = Field(default_factory=EntityMemoryGroups)
    items: EntityMemoryGroups = Field(default_factory=EntityMemoryGroups)
    inventory: InventoryView = Field(default_factory=InventoryView)


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
        del round_number  # Routing time is intentionally not exposed to participants.
        actor_id = runtime.actor_id
        room_id = state.root_room_of(actor_id)
        observations = runtime.observations[runtime.observation_cursor :]
        runtime.observation_cursor = len(runtime.observations)
        execution_notices = list(runtime.execution_notices)
        runtime.execution_notices.clear()
        observed_views = {
            view.id: view
            for view in environment.full_observations
            if view.change in {
                ScanObservationStatus.NEW,
                ScanObservationStatus.MOVED,
            }
        }
        character_groups = EntityMemoryGroups()
        item_groups = EntityMemoryGroups()
        inventory = InventoryView()

        for entity_id, known in runtime.knowledge.entities.items():
            entity = state.entities.get(entity_id)
            if entity is None or entity_id == actor_id or entity.kind == EntityKind.ROOM:
                continue

            placement = state.placements.get(entity_id)
            observed = observed_views.get(entity_id)
            if (
                isinstance(entity, Item)
                and placement is not None
                and placement.parent_id == actor_id
            ):
                view = self._current_view(state, entity_id, observed)
                target = (
                    inventory.attached
                    if placement.relation == PlacementRelation.ATTACHED
                    else inventory.inside
                )
                target.append(view)
                continue

            groups = (
                character_groups
                if entity.kind == EntityKind.CHARACTER
                else item_groups
            )
            if observed is not None:
                groups.new_or_changed.append(
                    self._current_view(state, entity_id, observed)
                )
                continue

            placement_unchanged = known.last_observed_placement == placement
            same_room = state.root_room_of(entity_id) == room_id
            visibility = (
                self.visibility.item_visibility(state, actor_id, entity_id)
                if isinstance(entity, Item)
                else self.visibility.base_visibility(state, actor_id, entity_id)
            )
            if placement_unchanged and same_room and visibility > 0.0:
                groups.visible_same_location.append(
                    EntityView(id=entity_id, name=entity.name, placement=placement)
                )
            else:
                groups.memories.append(
                    EntityView(
                        id=entity_id,
                        name=known.name,
                        placement=known.last_observed_placement,
                    )
                )

        return TurnContext(
            actor_id=actor_id,
            location=LocationView(id=room_id, name=state.room(room_id).name),
            observations_since_last_action=[item.text for item in observations],
            last_action_feedback=execution_notices,
            characters=character_groups,
            items=item_groups,
            inventory=inventory,
        )

    @staticmethod
    def _current_view(
        state: WorldState,
        entity_id: str,
        observed: EntityView | None,
    ) -> EntityView:
        entity = state.entities[entity_id]
        if observed is not None:
            return EntityView(
                id=entity_id,
                name=entity.name,
                placement=state.placements.get(entity_id),
                description=entity.description,
                change=observed.change,
            )
        return EntityView(
            id=entity_id,
            name=entity.name,
            placement=state.placements.get(entity_id),
        )
