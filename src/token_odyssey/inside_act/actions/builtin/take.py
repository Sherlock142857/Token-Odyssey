from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class TakeIntent(BaseActionIntent):
    kind: Literal["take"] = "take"
    target_entity_id: str


class TakeEventData(ActionEventData):
    item_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(TakeIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    if item is None:
        return reasons
    placement = context.state.placements[item.id]
    if placement.parent_id == context.actor_id:
        reasons.append(f"角色已经控制着 {item.name}")
    elif not context.query.is_accessible(context.actor_id, item.id):
        reasons.append(f"角色无法接触物品 {item.name}")
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(TakeIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    new = Placement(relation=PlacementRelation.ATTACHED, parent_id=context.actor_id)
    return ActionEffect(
        data=TakeEventData(item_id=intent.target_entity_id),
        mutations=[PlacementMutation(intent.target_entity_id, old, new)],
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    return {cast(TakeIntent, raw).target_entity_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    return f"{state.character(event.actor_id).name}拿起了「{state.item(event.data.item_id).name}」。"


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}伸手取走了一个被遮挡的物件。"


ACTION = ActionSpec(
    kind="take", intent_model=TakeIntent, event_model=TakeEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.6,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="拿起一个可接触物品",
)
