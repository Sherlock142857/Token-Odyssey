"""Visibility calculations over a canonical placement forest and Room graph."""

from __future__ import annotations

from token_odyssey.inside_act.domain.entities import Item
from token_odyssey.inside_act.domain.spatial import WorldState


class VisibilityService:
    def __init__(self) -> None:
        self._room_cache: dict[tuple[int, int, str, str], float] = {}
        self._base_cache: dict[tuple[int, int, str, str], float] = {}

    def room_visibility(
        self, state: WorldState, observer_room_id: str, source_room_id: str
    ) -> float:
        key = (id(state), state.revision, observer_room_id, source_room_id)
        if key not in self._room_cache:
            self._room_cache[key] = state.room_graph.visibility(
                observer_room_id, source_room_id
            )
        return self._room_cache[key]

    def base_visibility(
        self, state: WorldState, observer_entity_id: str, target_entity_id: str
    ) -> float:
        key = (id(state), state.revision, observer_entity_id, target_entity_id)
        if key not in self._base_cache:
            observer_room = state.root_room_of(observer_entity_id)
            target_room = state.root_room_of(target_entity_id)
            value = (
                state.edge_product_to_room(observer_entity_id)
                * self.room_visibility(state, observer_room, target_room)
                * state.edge_product_to_room(target_entity_id)
            )
            self._base_cache[key] = max(0.0, min(1.0, value))
        return self._base_cache[key]

    def item_visibility(
        self, state: WorldState, observer_entity_id: str, item_id: str
    ) -> float:
        item = state.item(item_id)
        assert isinstance(item, Item)
        return max(
            0.0,
            min(
                1.0,
                self.base_visibility(state, observer_entity_id, item_id)
                * item.intrinsic_visibility,
            ),
        )
