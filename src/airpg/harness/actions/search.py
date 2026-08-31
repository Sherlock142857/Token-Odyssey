from __future__ import annotations

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect
from airpg.models import SearchActionIntent


class SearchHandler:
    def validate(self, context: ActionContext, intent: SearchActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        if item is None:
            return reasons
        if not item.is_container:
            reasons.append(f"{item.name} 不是容器")
        elif not context.query.is_accessible(context.actor_id, item.id):
            reasons.append(f"角色无法接触容器 {item.name}")
        return reasons

    def plan(self, context: ActionContext, intent: SearchActionIntent) -> ActionEffect:
        return ActionEffect(
            data={"container_id": intent.target_item_id},
            detail_visibility=context.state.item_exposure(intent.target_item_id),
        )

