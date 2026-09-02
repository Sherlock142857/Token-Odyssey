from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.actions.contracts import (
    ActionContext,
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
)
from token_odyssey.inside_act.domain.events import (
    ActionEventData,
    WorldEvent,
    WorldMechanicTrigger,
)
from token_odyssey.inside_act.domain.spatial import WorldState


class OperateIntent(BaseActionIntent):
    kind: Literal["operate"] = "operate"
    target_entity_id: str


class OperateEventData(ActionEventData):
    target_entity_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(OperateIntent, raw)
    target, reasons = require_item(context, intent.target_entity_id)
    if target is not None and not context.query.is_accessible(context.actor_id, target.id):
        reasons.append(f"角色无法接触设备 {target.name}")
    if target is not None and context.state.mechanics.operation_for(target.id) is None:
        reasons.append(f"{target.name} 没有可用的 operate 机制")
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(OperateIntent, raw)
    return ActionEffect(
        data=OperateEventData(target_entity_id=intent.target_entity_id),
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_entity_id],
        mechanic_triggers=[
            WorldMechanicTrigger(target_entity_id=intent.target_entity_id)
        ],
    )


def references(raw: BaseActionIntent) -> set[str]:
    return {cast(OperateIntent, raw).target_entity_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return (
        f"{state.character(event.actor_id).name}操作了"
        f"「{state.item(event.data.target_entity_id).name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}伸手拨动了一处设备。"


ACTION = ActionSpec(
    kind="operate",
    intent_model=OperateIntent,
    event_model=OperateEventData,
    validate=validate,
    plan=plan,
    known_reference_extractor=references,
    intrinsic_visibility=0.5,
    render_full=render_full,
    render_partial=render_partial,
    prompt_usage="操作一个已知设备；设备结果由 WORLD 决定",
    prompt_requirements=("目标已知、可接触且声明了操作机制",),
    prompt_effect="记录操作动作，随后由 WORLD 输出设备的成功或失败反应",
    prompt_misuses=("operate 不用于搜索、展示或安装组件",),
    stale_after_move_recoverable=True,
)
