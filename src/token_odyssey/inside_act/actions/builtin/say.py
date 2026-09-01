from __future__ import annotations

from typing import Literal, cast

from pydantic import Field

from token_odyssey.inside_act.actions.contracts import (
    ActionContext,
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
)
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor
from token_odyssey.inside_act.domain.events import ActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class SayIntent(BaseActionIntent):
    kind: Literal["say"] = "say"
    target_character_ids: list[str] = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)


class SayEventData(ActionEventData):
    target_character_ids: list[str]
    content: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(SayIntent, raw)
    return context.query.same_room_character_reasons(
        context.actor_id, intent.target_character_ids, allow_self=False
    )


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(SayIntent, raw)
    targets = list(dict.fromkeys(intent.target_character_ids))
    return ActionEffect(
        data=SayEventData(target_character_ids=targets, content=intent.content),
        anchors=[actor_anchor(context.actor_id)],
        guaranteed_observer_ids=targets,
    )


def references(raw: BaseActionIntent) -> set[str]:
    return set(cast(SayIntent, raw).target_character_ids)


def render_full(state: WorldState, event: WorldEvent) -> str:
    actor = state.character(event.actor_id)
    targets = "、".join(
        state.character(target_id).name for target_id in event.data.target_character_ids
    )
    return f"{actor.name}对{targets}说：“{event.data.content}”"


def render_partial(state: WorldState, event: WorldEvent) -> str:
    actor = state.character(event.actor_id)
    return f"你听见{actor.name}发出说话声，内容没有辨清。"


ACTION = ActionSpec(
    kind="say",
    intent_model=SayIntent,
    event_model=SayEventData,
    validate=validate,
    plan=plan,
    known_reference_extractor=references,
    intrinsic_visibility=0.9,
    render_full=render_full,
    render_partial=render_partial,
    prompt_usage="向当前同房间角色说话",
)
