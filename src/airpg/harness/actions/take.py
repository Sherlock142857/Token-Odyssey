from __future__ import annotations

from copy import deepcopy

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect, StateMutation, location_payload
from airpg.models import Location, LocationKind, TakeActionIntent


class TakeHandler:
    def validate(self, context: ActionContext, intent: TakeActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        if item is None:
            return reasons
        if context.query.is_controlled_by(context.actor_id, item.id):
            reasons.append(f"角色已经控制着 {item.name}")
        elif not context.query.is_accessible(context.actor_id, item.id):
            reasons.append(f"角色无法接触物品 {item.name}")
        return reasons

    def plan(self, context: ActionContext, intent: TakeActionIntent) -> ActionEffect:
        item = context.state.items[intent.target_item_id]
        old_location = deepcopy(item.location)
        new_location = Location(kind=LocationKind.HELD, target_id=context.actor_id)
        return ActionEffect(
            data={
                "item_id": item.id,
                "from_location": location_payload(old_location),
                "to_location": location_payload(new_location),
            },
            mutations=[
                StateMutation("item", item.id, "location", old_location, new_location)
            ],
            detail_visibility=context.state.item_exposure(item.id),
        )

