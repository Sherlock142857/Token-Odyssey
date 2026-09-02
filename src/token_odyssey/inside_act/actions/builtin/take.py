from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent, PlacementMutation
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, ExecutionNotice, WorldEvent
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
    if not context.query.is_accessible(context.actor_id, item.id):
        reasons.append(
            f"无法 take「{item.name}」：它不在同一房间、或正由其他角色控制"
        )
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(TakeIntent, raw)
    old = context.state.placements[intent.target_entity_id].model_copy(deep=True)
    if old.parent_id == context.actor_id and old.relation == PlacementRelation.ATTACHED:
        return ActionEffect(
            data=TakeEventData(item_id=intent.target_entity_id),
            emit_event=False,
            notices=[
                ExecutionNotice(
                    code="redundant_take",
                    message=f"take 未执行：{context.state.item(intent.target_entity_id).name}已经由你公开拿持",
                )
            ],
        )
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
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}伸手取走了一个被遮挡的物件。"


ACTION = ActionSpec(
    kind="take", intent_model=TakeIntent, event_model=TakeEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.6,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="拿起一个可接触物品，或把藏在自己身上的物品公开取出",
    prompt_requirements=("物品已知", "物品可接触且未由他人控制",),
    prompt_effect="物品变为 attached:自己（公开拿持）",
    prompt_misuses=("已经 attached:自己时不要重复 take，改用 show/place/give/hide",),
    stale_after_move_recoverable=True,
)
