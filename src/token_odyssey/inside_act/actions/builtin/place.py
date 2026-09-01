from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class PlaceIntent(BaseActionIntent):
    kind: Literal["place"] = "place"
    target_entity_id: str
    container_id: str


class PlaceEventData(ActionEventData):
    item_id: str
    container_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(PlaceIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    container = context.state.entities.get(intent.container_id)
    if container is None:
        reasons.append(f"未知容器 {intent.container_id!r}")
        return reasons
    if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
        reasons.append(f"角色没有控制物品 {item.name}")
    if not container.is_container:
        reasons.append(f"{container.name} 不是容器")
    elif not context.query.is_accessible(context.actor_id, container.id):
        reasons.append(f"角色无法接触容器 {container.name}")
    if item is not None:
        if item.id == container.id or context.state.is_descendant(container.id, item.id):
            reasons.append("放置会形成循环包含")
        else:
            size_reason = context.query.inside_size_reason(item.id, container.id)
            if size_reason:
                reasons.append(size_reason)
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(PlaceIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    new = Placement(relation=PlacementRelation.INSIDE, parent_id=intent.container_id)
    return ActionEffect(
        data=PlaceEventData(item_id=intent.target_entity_id, container_id=intent.container_id),
        mutations=[PlacementMutation(intent.target_entity_id, old, new)],
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    intent = cast(PlaceIntent, raw)
    return {intent.target_entity_id, intent.container_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    return (
        f"{state.character(event.actor_id).name}把「{state.item(event.data.item_id).name}」"
        f"放进了「{state.entities[event.data.container_id].name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}把一个物件放入一处容器。"


ACTION = ActionSpec(
    kind="place", intent_model=PlaceIntent, event_model=PlaceEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.6,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="把控制中的物品放进容器",
    prompt_requirements=("自己控制该物品", "容器已知、可接触且尺寸足够",),
    prompt_effect="物品变为 inside:容器，通常不再由原角色控制",
    prompt_misuses=("不能形成循环包含", "不能用 place 表示操作或安装设备",),
)
