from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.entities import Character
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class PlaceIntent(BaseActionIntent):
    kind: Literal["place"] = "place"
    target_entity_id: str
    container_id: str
    relation: PlacementRelation


class PlaceEventData(ActionEventData):
    item_id: str
    container_id: str
    relation: PlacementRelation


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(PlaceIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    container = context.state.entities.get(intent.container_id)
    if container is None:
        reasons.append(f"未知容器 {intent.container_id!r}")
        return reasons
    if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
        reasons.append(f"角色没有控制物品 {item.name}")
    if intent.relation == PlacementRelation.INSIDE and not container.is_container:
        reasons.append(f"{container.name} 不是容器，不能放入")
    elif intent.relation == PlacementRelation.ATTACHED and isinstance(container, Character):
        reasons.append("不能用 place 把物品附着到角色身上；请使用 give 或 hide")
    elif (
        item is not None
        and intent.relation == PlacementRelation.ATTACHED
        and context.state.mechanics.installation_allowed(item.id, container.id)
    ):
        reasons.append(f"{item.name} 与 {container.name} 是安装配对；请使用 install")
    elif not context.query.is_accessible(context.actor_id, container.id):
        reasons.append(f"角色无法接触容器 {container.name}")
    if item is not None:
        if item.id == container.id or context.state.is_descendant(container.id, item.id):
            reasons.append("放置会形成循环包含")
        elif intent.relation == PlacementRelation.INSIDE:
            size_reason = context.query.inside_size_reason(item.id, container.id)
            if size_reason:
                reasons.append(size_reason)
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(PlaceIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    new = Placement(relation=intent.relation, parent_id=intent.container_id)
    return ActionEffect(
        data=PlaceEventData(
            item_id=intent.target_entity_id,
            container_id=intent.container_id,
            relation=intent.relation,
        ),
        mutations=[PlacementMutation(intent.target_entity_id, old, new)],
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    intent = cast(PlaceIntent, raw)
    return {intent.target_entity_id, intent.container_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    verb = "放进了" if event.data.relation == PlacementRelation.INSIDE else "放在了"
    return (
        f"{state.character(event.actor_id).name}把「{state.item(event.data.item_id).name}」"
        f"{verb}「{state.entities[event.data.container_id].name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    verb = "放入一处容器" if event.data.relation == PlacementRelation.INSIDE else "放到一处位置"
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}把一个物件{verb}。"


ACTION = ActionSpec(
    kind="place", intent_model=PlaceIntent, event_model=PlaceEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.6,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="把控制中的物品放到目标上（attached）或放入容器（inside）",
    prompt_requirements=("自己控制该物品", "relation 必须为 attached 或 inside", "目标已知且可接触",),
    prompt_effect="物品变为 relation:目标；放到自己控制链之外会释放控制权",
    prompt_misuses=("不能形成循环包含", "不能用 place 表示操作或安装设备",),
    stale_after_move_recoverable=True,
)
