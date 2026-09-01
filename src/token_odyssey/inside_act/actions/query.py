"""Read-only world queries shared by action validators."""

from __future__ import annotations

from token_odyssey.inside_act.domain.entities import Character, EntityKind, Item
from token_odyssey.inside_act.domain.spatial import PlacementRelation, WorldState


class WorldQuery:
    def __init__(self, state: WorldState) -> None:
        self.state = state

    def is_controlled_by(self, actor_id: str, entity_id: str) -> bool:
        return self.state.controller_of(entity_id) == actor_id

    def is_accessible(self, actor_id: str, entity_id: str) -> bool:
        if self.state.root_room_of(actor_id) != self.state.root_room_of(entity_id):
            return False
        controller = self.state.controller_of(entity_id)
        return controller is None or controller == actor_id

    def same_room_character_reasons(
        self, actor_id: str, target_ids: list[str], *, allow_self: bool = False
    ) -> list[str]:
        actor_room = self.state.root_room_of(actor_id)
        reasons: list[str] = []
        for target_id in dict.fromkeys(target_ids):
            target = self.state.entities.get(target_id)
            if not isinstance(target, Character):
                reasons.append(f"未知目标角色 {target_id!r}")
            elif not allow_self and target_id == actor_id:
                reasons.append("不能把自己指定为对象")
            elif self.state.root_room_of(target_id) != actor_room:
                reasons.append(f"目标 {target.name} 不在同一房间，不能直接交互")
        return reasons

    def inside_size_reason(self, child_id: str, parent_id: str) -> str | None:
        child = self.state.entities[child_id]
        parent = self.state.entities[parent_id]
        child_size = getattr(child, "size_class", None)
        if child_size is None or parent.kind == EntityKind.ROOM:
            return None
        if isinstance(parent, Item):
            limit = parent.size_class
        elif isinstance(parent, Character):
            limit = self.state.rules.actor_concealment_size_limit
        else:
            return None
        if child_size > limit:
            return f"{child.name} 大小等级 {child_size} 超过 {parent.name} 的容纳等级 {limit}"
        return None
