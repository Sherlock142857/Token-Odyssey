from __future__ import annotations

from airpg.harness.actions.base import ActionContext
from airpg.harness.effects import ActionEffect, StateMutation
from airpg.models import MoveActionIntent


class MoveHandler:
    def validate(self, context: ActionContext, intent: MoveActionIntent) -> list[str]:
        actor = context.state.actors[context.actor_id]
        if intent.destination_room_id not in context.state.rooms:
            return [f"不存在房间 {intent.destination_room_id!r}"]
        if intent.destination_room_id == actor.room_id:
            return ["角色已经在目标房间"]
        return []

    def plan(self, context: ActionContext, intent: MoveActionIntent) -> ActionEffect:
        actor = context.state.actors[context.actor_id]
        return ActionEffect(
            data={"from_room_id": actor.room_id, "to_room_id": intent.destination_room_id},
            mutations=[
                StateMutation(
                    target_type="actor",
                    target_id=actor.id,
                    field_name="room_id",
                    before=actor.room_id,
                    after=intent.destination_room_id,
                )
            ],
        )

