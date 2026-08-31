from __future__ import annotations

from copy import deepcopy

from airpg.harness.actions.base import ActionContext, require_item
from airpg.harness.effects import ActionEffect, StateMutation, location_payload
from airpg.models import Location, LocationKind, PlaceActionIntent


class PlaceHandler:
    def validate(self, context: ActionContext, intent: PlaceActionIntent) -> list[str]:
        item, reasons = require_item(context.state, intent.target_item_id)
        container, container_reasons = require_item(context.state, intent.container_id)
        reasons.extend(container_reasons)
        if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
            reasons.append(f"角色没有控制物品 {item.name}")
        if container is not None:
            if not container.is_container:
                reasons.append(f"{container.name} 不是容器")
            elif not context.query.is_accessible(context.actor_id, container.id):
                reasons.append(f"角色无法接触容器 {container.name}")
        if item is not None and container is not None:
            if item.id == container.id or context.query.is_descendant(container.id, item.id):
                reasons.append("放置会形成循环包含")
            elif container.container_capacity is not None:
                used = context.query.used_capacity(container.id, excluding_item_id=item.id)
                if used + item.size > container.container_capacity:
                    reasons.append(
                        f"{container.name} 容量不足：已用 {used}，物品大小 {item.size}，容量 {container.container_capacity}"
                    )
        return reasons

    def plan(self, context: ActionContext, intent: PlaceActionIntent) -> ActionEffect:
        item = context.state.items[intent.target_item_id]
        container = context.state.items[intent.container_id]
        old_location = deepcopy(item.location)
        old_exposure = context.state.item_exposure(item.id)
        new_location = Location(kind=LocationKind.CONTAINER, target_id=container.id)
        new_exposure = item.base_visibility * container.content_visibility * context.state.item_exposure(container.id)
        return ActionEffect(
            data={
                "item_id": item.id,
                "container_id": container.id,
                "from_location": location_payload(old_location),
                "to_location": location_payload(new_location),
            },
            mutations=[StateMutation("item", item.id, "location", old_location, new_location)],
            detail_visibility=max(old_exposure, new_exposure),
        )

