from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class GiveIntent(BaseActionIntent):
    kind: Literal["give"] = "give"
    target_entity_id: str
    recipient_id: str


class GiveEventData(ActionEventData):
    item_id: str
    recipient_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(GiveIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
        reasons.append(f"角色没有控制物品 {item.name}")
    reasons.extend(context.query.same_room_character_reasons(context.actor_id, [intent.recipient_id]))
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(GiveIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    new = Placement(relation=PlacementRelation.ATTACHED, parent_id=intent.recipient_id)
    return ActionEffect(
        data=GiveEventData(item_id=intent.target_entity_id, recipient_id=intent.recipient_id),
        mutations=[PlacementMutation(intent.target_entity_id, old, new)],
        anchors=[actor_anchor(context.actor_id)],
        guaranteed_observer_ids=[intent.recipient_id],
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    intent = cast(GiveIntent, raw)
    return {intent.target_entity_id, intent.recipient_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    return (
        f"{state.character(event.actor_id).name}把「{state.item(event.data.item_id).name}」"
        f"交给了{state.character(event.data.recipient_id).name}。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}与另一人交接了一个物件。"


ACTION = ActionSpec(
    kind="give", intent_model=GiveIntent, event_model=GiveEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.8,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="把控制中的物品交给同房间角色",
    prompt_requirements=("自己控制该物品", "接收者与自己同房间",),
    prompt_effect="物品变为 attached:接收者；接收者保证观察到交付",
    prompt_misuses=("公共位置的物品要先在更早 frame take",),
    stale_after_move_recoverable=True,
)
