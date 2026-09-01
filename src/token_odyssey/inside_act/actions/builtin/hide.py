from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class HideIntent(BaseActionIntent):
    kind: Literal["hide"] = "hide"
    target_entity_id: str


class HideEventData(ActionEventData):
    item_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(HideIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    if item is None:
        return reasons
    if not context.query.is_controlled_by(context.actor_id, item.id):
        reasons.append(f"角色没有控制物品 {item.name}")
    size_reason = context.query.inside_size_reason(item.id, context.actor_id)
    if size_reason:
        reasons.append(size_reason)
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(HideIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    new = Placement(relation=PlacementRelation.INSIDE, parent_id=context.actor_id)
    return ActionEffect(
        data=HideEventData(item_id=intent.target_entity_id),
        mutations=[PlacementMutation(intent.target_entity_id, old, new)],
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    return {cast(HideIntent, raw).target_entity_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    return (
        f"{state.character(event.actor_id).name}把"
        f"「{state.item(event.data.item_id).name}」收进了身上遮蔽的位置。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}的手移向身上，目标被身体遮挡。"


ACTION = ActionSpec(
    kind="hide", intent_model=HideIntent, event_model=HideEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.4,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="把控制中的小型物品藏在身上",
    prompt_requirements=("自己控制该物品", "物品不超过角色统一藏匿尺寸上限",),
    prompt_effect="物品变为 inside:自己（藏匿）；之后可用 take 重新公开取出",
    prompt_misuses=("hide 不是销毁物品，仍会按可见度被他人发现",),
)
