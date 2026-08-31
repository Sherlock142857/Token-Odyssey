from __future__ import annotations

from copy import deepcopy

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect, StateMutation, location_payload
from airpg.models import GiveActionIntent, Location, LocationKind


class GiveHandler:
    def validate(self, context: ActionContext, intent: GiveActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
            reasons.append(f"角色没有控制物品 {item.name}")
        reasons.extend(
            context.query.same_room_target_reasons(
                context.actor_id, [intent.recipient_id], allow_self=False
            )
        )
        return reasons

    def plan(self, context: ActionContext, intent: GiveActionIntent) -> ActionEffect:
        item = context.state.items[intent.target_item_id]
        old_location = deepcopy(item.location)
        new_location = Location(kind=LocationKind.HELD, target_id=intent.recipient_id)
        return ActionEffect(
            data={
                "item_id": item.id,
                "recipient_id": intent.recipient_id,
                "from_location": location_payload(old_location),
                "to_location": location_payload(new_location),
            },
            mutations=[StateMutation("item", item.id, "location", old_location, new_location)],
            direct_observer_ids=[intent.recipient_id],
        )

