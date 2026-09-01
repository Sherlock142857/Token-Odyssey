from __future__ import annotations

from typing import Literal, cast

from pydantic import Field

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class ShowIntent(BaseActionIntent):
    kind: Literal["show"] = "show"
    target_entity_id: str
    audience_ids: list[str] = Field(min_length=1)


class ShowEventData(ActionEventData):
    item_id: str
    audience_ids: list[str]


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(ShowIntent, raw)
    item, reasons = require_item(context, intent.target_entity_id)
    if item is not None and not context.query.is_controlled_by(context.actor_id, item.id):
        reasons.append(f"角色没有控制物品 {item.name}")
    reasons.extend(context.query.same_room_character_reasons(context.actor_id, intent.audience_ids))
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(ShowIntent, raw)
    audiences = list(dict.fromkeys(intent.audience_ids))
    return ActionEffect(
        data=ShowEventData(item_id=intent.target_entity_id, audience_ids=audiences),
        anchors=[actor_anchor(context.actor_id)],
        guaranteed_observer_ids=audiences,
        knowledge_entity_ids=[intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    intent = cast(ShowIntent, raw)
    return {intent.target_entity_id, *intent.audience_ids}


def render_full(state: WorldState, event: WorldEvent) -> str:
    targets = "、".join(state.character(target_id).name for target_id in event.data.audience_ids)
    return (
        f"{state.character(event.actor_id).name}向{targets}展示了"
        f"「{state.item(event.data.item_id).name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}举起一个物件给人观看。"


ACTION = ActionSpec(
    kind="show", intent_model=ShowIntent, event_model=ShowEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=1.0,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="向同房间角色展示控制中的物品",
)
