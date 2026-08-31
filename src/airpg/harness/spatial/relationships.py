"""Space and item-exposure relationships used by perception."""

from airpg.models import WorldState


class SpatialRelationships:
    def __init__(self, state: WorldState) -> None:
        self.state = state

    def item_room(self, item_id: str) -> str:
        return self.state.effective_room_of_item(item_id)

    def item_exposure(self, item_id: str) -> float:
        return self.state.item_exposure(item_id)

    def visibility(self, observer_room: str, source_room: str) -> float:
        return self.state.spatial_visibility(observer_room, source_room)

