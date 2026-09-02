"""Canonical placement forest, directed Room graph, and spatial queries."""

from __future__ import annotations

import heapq
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.entities import Character, Entity, EntityKind, Item, Room


class PlacementRelation(StrEnum):
    INSIDE = "inside"
    ATTACHED = "attached"


class Placement(StrictModel):
    relation: PlacementRelation
    parent_id: str = Field(min_length=1)


class WorldRules(StrictModel):
    actor_container_visibility: float = Field(default=0.4, ge=0.0, le=1.0)
    actor_concealment_size_limit: int = Field(default=3, ge=1, le=10)
    actor_size_class: int = Field(default=6, ge=1, le=10)
    default_room_container_visibility: float = Field(default=1.0, ge=0.0, le=1.0)
    partial_visibility_factor: float = Field(default=1.5, ge=1.0, le=10.0)


class MechanicReaction(StrictModel):
    full_text: str = Field(min_length=1)
    partial_text: str = Field(min_length=1)
    intrinsic_visibility: float = Field(default=1.0, ge=0.0, le=1.0)


class InstallationRule(StrictModel):
    component_id: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)


class OperationRule(StrictModel):
    id: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)
    required_installed_component_ids: list[str] = Field(default_factory=list)
    success: MechanicReaction
    failure: MechanicReaction


class WorldMechanics(StrictModel):
    installations: list[InstallationRule] = Field(default_factory=list)
    operations: list[OperationRule] = Field(default_factory=list)

    def installation_allowed(self, component_id: str, target_entity_id: str) -> bool:
        return any(
            rule.component_id == component_id
            and rule.target_entity_id == target_entity_id
            for rule in self.installations
        )

    def operation_for(self, target_entity_id: str) -> OperationRule | None:
        return next(
            (
                rule
                for rule in self.operations
                if rule.target_entity_id == target_entity_id
            ),
            None,
        )


class RoomVisibilityGraph(StrictModel):
    """Directed observer-room -> source-room visibility edges."""

    edges: dict[str, dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]] = Field(
        default_factory=dict
    )

    def visibility(self, observer_room_id: str, source_room_id: str) -> float:
        if observer_room_id == source_room_id:
            return 1.0
        # Max-product Dijkstra. Edge weights never exceed one, so a settled node is optimal.
        best: dict[str, float] = {observer_room_id: 1.0}
        pending: list[tuple[float, str]] = [(-1.0, observer_room_id)]
        while pending:
            negative_score, room_id = heapq.heappop(pending)
            score = -negative_score
            if score < best.get(room_id, 0.0):
                continue
            if room_id == source_room_id:
                return score
            for target_id, edge_weight in self.edges.get(room_id, {}).items():
                if edge_weight <= 0.0:
                    continue
                candidate = score * edge_weight
                if candidate > best.get(target_id, 0.0):
                    best[target_id] = candidate
                    heapq.heappush(pending, (-candidate, target_id))
        return 0.0


class WorldState(StrictModel):
    entities: dict[str, Entity]
    placements: dict[str, Placement]
    room_graph: RoomVisibilityGraph = Field(default_factory=RoomVisibilityGraph)
    rules: WorldRules = Field(default_factory=WorldRules)
    mechanics: WorldMechanics = Field(default_factory=WorldMechanics)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_world(self) -> "WorldState":
        if not self.entities:
            raise ValueError("world must contain at least one entity")
        for entity_id, entity in self.entities.items():
            if entity_id != entity.id:
                raise ValueError(
                    f"entity mapping key {entity_id!r} does not match id {entity.id!r}"
                )
            if entity.protector_id is not None and entity.protector_id not in self.entities:
                raise ValueError(
                    f"entity {entity_id!r} references unknown protector {entity.protector_id!r}"
                )

        room_ids = self.room_ids
        if not room_ids:
            raise ValueError("world must contain at least one Room")
        for entity_id, entity in self.entities.items():
            if entity.kind == EntityKind.ROOM:
                if entity_id in self.placements:
                    raise ValueError(f"Room {entity_id!r} cannot have a placement")
            elif entity_id not in self.placements:
                raise ValueError(f"non-Room entity {entity_id!r} must have exactly one placement")
        for child_id, placement in self.placements.items():
            child = self.entities.get(child_id)
            if child is None:
                raise ValueError(f"placement references unknown child {child_id!r}")
            if child.kind == EntityKind.ROOM:
                raise ValueError(f"Room {child_id!r} cannot have a placement")
            parent = self.entities.get(placement.parent_id)
            if parent is None:
                raise ValueError(
                    f"entity {child_id!r} references unknown parent {placement.parent_id!r}"
                )
            if placement.relation == PlacementRelation.INSIDE:
                if not parent.is_container:
                    raise ValueError(f"entity {child_id!r} is inside non-container {parent.id!r}")
                self._validate_inside_size(child, parent)

        for entity_id in self.entities:
            if entity_id not in room_ids:
                self._validate_path_to_room(entity_id)

        for observer_room, row in self.room_graph.edges.items():
            if observer_room not in room_ids:
                raise ValueError(f"Room graph has unknown observer Room {observer_room!r}")
            for source_room in row:
                if source_room not in room_ids:
                    raise ValueError(f"Room graph has unknown source Room {source_room!r}")
        installation_pairs: set[tuple[str, str]] = set()
        for rule in self.mechanics.installations:
            pair = (rule.component_id, rule.target_entity_id)
            if pair in installation_pairs:
                raise ValueError(f"duplicate installation rule {pair!r}")
            installation_pairs.add(pair)
            if rule.component_id not in self.item_ids:
                raise ValueError(
                    f"installation references non-Item component {rule.component_id!r}"
                )
            if rule.target_entity_id not in self.item_ids:
                raise ValueError(
                    f"installation references non-Item target {rule.target_entity_id!r}"
                )

        operation_ids: set[str] = set()
        operation_targets: set[str] = set()
        for rule in self.mechanics.operations:
            if rule.id in operation_ids:
                raise ValueError(f"duplicate operation mechanic id {rule.id!r}")
            if rule.target_entity_id in operation_targets:
                raise ValueError(
                    f"duplicate operation target {rule.target_entity_id!r}"
                )
            operation_ids.add(rule.id)
            operation_targets.add(rule.target_entity_id)
            if rule.target_entity_id not in self.item_ids:
                raise ValueError(
                    f"operation references non-Item target {rule.target_entity_id!r}"
                )
            for component_id in rule.required_installed_component_ids:
                if (component_id, rule.target_entity_id) not in installation_pairs:
                    raise ValueError(
                        f"operation {rule.id!r} requires undeclared installation "
                        f"{(component_id, rule.target_entity_id)!r}"
                    )
        return self

    @property
    def room_ids(self) -> set[str]:
        return {
            entity_id
            for entity_id, entity in self.entities.items()
            if entity.kind == EntityKind.ROOM
        }

    @property
    def character_ids(self) -> list[str]:
        return [
            entity_id
            for entity_id, entity in self.entities.items()
            if entity.kind == EntityKind.CHARACTER
        ]

    @property
    def item_ids(self) -> list[str]:
        return [
            entity_id
            for entity_id, entity in self.entities.items()
            if entity.kind == EntityKind.ITEM
        ]

    def room(self, entity_id: str) -> Room:
        entity = self.entities[entity_id]
        if not isinstance(entity, Room):
            raise TypeError(f"entity {entity_id!r} is not a Room")
        return entity

    def character(self, entity_id: str) -> Character:
        entity = self.entities[entity_id]
        if not isinstance(entity, Character):
            raise TypeError(f"entity {entity_id!r} is not a Character")
        return entity

    def item(self, entity_id: str) -> Item:
        entity = self.entities[entity_id]
        if not isinstance(entity, Item):
            raise TypeError(f"entity {entity_id!r} is not an Item")
        return entity

    def children_of(self, parent_id: str) -> list[str]:
        return [
            child_id
            for child_id, placement in self.placements.items()
            if placement.parent_id == parent_id
        ]

    def ancestors_of(self, entity_id: str) -> list[str]:
        ancestors: list[str] = []
        current_id = entity_id
        while current_id in self.placements:
            current_id = self.placements[current_id].parent_id
            ancestors.append(current_id)
        return ancestors

    def root_room_of(self, entity_id: str) -> str:
        entity = self.entities[entity_id]
        if entity.kind == EntityKind.ROOM:
            return entity_id
        ancestors = self.ancestors_of(entity_id)
        if not ancestors or ancestors[-1] not in self.room_ids:
            raise RuntimeError(f"entity {entity_id!r} does not terminate at a Room")
        return ancestors[-1]

    def edge_product_to_room(self, entity_id: str) -> float:
        factor = 1.0
        current_id = entity_id
        while current_id in self.placements:
            placement = self.placements[current_id]
            parent = self.entities[placement.parent_id]
            if placement.relation == PlacementRelation.INSIDE:
                factor *= parent.container_visibility
            current_id = placement.parent_id
        return factor

    def controller_of(self, entity_id: str) -> str | None:
        for ancestor_id in self.ancestors_of(entity_id):
            if self.entities[ancestor_id].kind == EntityKind.CHARACTER:
                return ancestor_id
        return None

    def is_descendant(self, candidate_id: str, ancestor_id: str) -> bool:
        return ancestor_id in self.ancestors_of(candidate_id)

    def _validate_path_to_room(self, entity_id: str) -> None:
        seen = {entity_id}
        current_id = entity_id
        while current_id in self.placements:
            parent_id = self.placements[current_id].parent_id
            if parent_id in seen:
                raise ValueError(f"placement cycle involving entity {entity_id!r}")
            seen.add(parent_id)
            current_id = parent_id
        if current_id not in self.room_ids:
            raise ValueError(f"entity {entity_id!r} parent chain does not terminate at a Room")

    def _validate_inside_size(self, child: Entity, parent: Entity) -> None:
        child_size = getattr(child, "size_class", None)
        if child_size is None or parent.kind == EntityKind.ROOM:
            return
        if isinstance(parent, Item):
            limit = parent.size_class
        elif isinstance(parent, Character):
            limit = self.rules.actor_concealment_size_limit
        else:
            return
        if child_size > limit:
            raise ValueError(
                f"entity {child.id!r} size class {child_size} exceeds "
                f"container {parent.id!r} limit {limit}"
            )
