"""Shared read-only world queries used by action handlers."""

from __future__ import annotations

from airpg.models import LocationKind, WorldState


class WorldQuery:
    def __init__(self, state: WorldState) -> None:
        self.state = state

    def is_controlled_by(self, actor_id: str, item_id: str) -> bool:
        location = self.state.items[item_id].location
        return location.kind in {LocationKind.HELD, LocationKind.HIDDEN, LocationKind.ATTACHED} and (
            location.target_id == actor_id
        )

    def is_accessible(self, actor_id: str, item_id: str) -> bool:
        actor = self.state.actors[actor_id]
        item = self.state.items[item_id]
        if self.state.effective_room_of_item(item_id) != actor.room_id:
            return False
        location = item.location
        if location.kind in {LocationKind.HELD, LocationKind.HIDDEN, LocationKind.ATTACHED}:
            return location.target_id == actor_id
        if location.kind == LocationKind.CONTAINER:
            return self.is_accessible(actor_id, location.target_id)
        return True

    def is_descendant(self, candidate_id: str, ancestor_id: str) -> bool:
        current = self.state.items[candidate_id]
        seen: set[str] = set()
        while current.location.kind == LocationKind.CONTAINER:
            parent_id = current.location.target_id
            if parent_id == ancestor_id:
                return True
            if parent_id in seen:
                return False
            seen.add(parent_id)
            current = self.state.items[parent_id]
        return False

    def used_capacity(self, container_id: str, excluding_item_id: str | None = None) -> int:
        return sum(
            child.size
            for child in self.state.items.values()
            if child.location.kind == LocationKind.CONTAINER
            and child.location.target_id == container_id
            and child.id != excluding_item_id
        )

    def same_room_target_reasons(
        self, actor_id: str, target_ids: list[str], *, allow_self: bool = False
    ) -> list[str]:
        actor = self.state.actors[actor_id]
        reasons: list[str] = []
        for target_id in dict.fromkeys(target_ids):
            target = self.state.actors.get(target_id)
            if target is None:
                reasons.append(f"未知目标角色 {target_id!r}")
            elif not allow_self and target_id == actor_id:
                reasons.append("不能把自己指定为对象")
            elif target.room_id != actor.room_id:
                reasons.append(f"目标 {target.name} 不在同一房间，不能直接交互")
        return reasons

