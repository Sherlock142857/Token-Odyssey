from __future__ import annotations

from copy import deepcopy

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect, StateMutation, location_payload
from airpg.models import HideActionIntent, Location, LocationKind


class HideHandler:
    def validate(self, context: ActionContext, intent: HideActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        if item is None:
            return reasons
        actor = context.state.actors[context.actor_id]
        if not context.query.is_controlled_by(context.actor_id, item.id):
            reasons.append(f"角色没有控制物品 {item.name}")
        elif item.size > actor.hide_capacity:
            reasons.append(
                f"{item.name} 大小为 {item.size}，超过角色藏匿容量 {actor.hide_capacity}"
            )
        return reasons

    def plan(self, context: ActionContext, intent: HideActionIntent) -> ActionEffect:
        item = context.state.items[intent.target_item_id]
        old_location = deepcopy(item.location)
        new_location = Location(kind=LocationKind.HIDDEN, target_id=context.actor_id)
        return ActionEffect(
            data={
                "item_id": item.id,
                "from_location": location_payload(old_location),
                "to_location": location_payload(new_location),
            },
            mutations=[StateMutation("item", item.id, "location", old_location, new_location)],
            detail_visibility=context.state.item_exposure(item.id),
        )

