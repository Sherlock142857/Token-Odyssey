from __future__ import annotations

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect
from airpg.models import ShowActionIntent


class ShowHandler:
    def validate(self, context: ActionContext, intent: ShowActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
            reasons.append(f"角色没有控制物品 {item.name}")
        reasons.extend(
            context.query.same_room_target_reasons(
                context.actor_id, intent.audience_ids, allow_self=False
            )
        )
        return reasons

    def plan(self, context: ActionContext, intent: ShowActionIntent) -> ActionEffect:
        return ActionEffect(
            data={
                "item_id": intent.target_item_id,
                "audience_ids": list(dict.fromkeys(intent.audience_ids)),
            },
            direct_observer_ids=list(dict.fromkeys(intent.audience_ids)),
        )

