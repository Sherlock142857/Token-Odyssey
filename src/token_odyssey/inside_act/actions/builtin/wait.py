from __future__ import annotations

from typing import Literal

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor
from token_odyssey.inside_act.domain.events import EmptyActionEventData, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class WaitIntent(BaseActionIntent):
    kind: Literal["wait"] = "wait"


def render_full(state: WorldState, event: WorldEvent) -> str:
    return f"{state.character(event.actor_id).name}暂时停留在原处。"


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}停留了一会儿。"


ACTION = ActionSpec(
    kind="wait", intent_model=WaitIntent, event_model=EmptyActionEventData, validate=lambda _c, _i: [],
    plan=lambda c, _i: ActionEffect(anchors=[actor_anchor(c.actor_id)]),
    known_reference_extractor=lambda _: set(), intrinsic_visibility=0.2,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="不执行物理动作",
    prompt_requirements=(),
    prompt_effect="世界位置不变，但产生一次可观察的停留事件",
    prompt_misuses=("与其他 action 同时提交通常是冗余的",),
)
