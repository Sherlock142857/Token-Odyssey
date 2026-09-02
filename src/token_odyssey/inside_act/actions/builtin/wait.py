from __future__ import annotations

from typing import Literal

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent
from token_odyssey.inside_act.domain.events import EmptyActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class WaitIntent(BaseActionIntent):
    kind: Literal["wait"] = "wait"


def render_full(state: WorldState, event: WorldEvent) -> str:
    return f"{state.character(event.actor_id).name}暂时停留在原处。"


def render_partial(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}停留了一会儿。"


ACTION = ActionSpec(
    kind="wait", intent_model=WaitIntent, event_model=EmptyActionEventData, validate=lambda _c, _i: [],
    plan=lambda _c, _i: ActionEffect(emit_event=False),
    known_reference_extractor=lambda _: set(), intrinsic_visibility=0.2,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="不执行物理动作",
    prompt_requirements=(),
    prompt_effect="主动放弃本次行动权；不改变状态，也不产生 World Event",
    prompt_misuses=("必须是整份 TurnPlan 中唯一的 action",),
    must_be_exclusive=True,
)
