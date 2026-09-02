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
    target_id: str


class OperateEventData(ActionEventData):
    target_entity_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(OperateIntent, raw)
    target, reasons = require_item(context, intent.target_id)
    if target is not None and not context.query.is_accessible(context.actor_id, target.id):
        reasons.append(f"角色无法接触物品 {target.name}")
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(OperateIntent, raw)
    return ActionEffect(
        data=OperateEventData(target_entity_id=intent.target_id),
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.target_id],
        mechanic_triggers=[
            WorldMechanicTrigger(target_entity_id=intent.target_id)
        ],
    )


def references(raw: BaseActionIntent) -> set[str]:
    return {cast(OperateIntent, raw).target_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return (
        f"{state.character(event.actor_id).name}操作了"
        f"「{state.item(event.data.target_entity_id).name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}伸手操作了一个物品。"


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
    prompt_usage="操作一个已知物品；结果由物品类型和 WORLD 机制决定",
    prompt_requirements=("目标是已知且可接触的物品",),
    prompt_effect="有机制时由 WORLD 输出结果；无机制容器按 search 处理，其他物品返回无效果",
    prompt_misuses=("operate 不用于搜索、展示或安装组件",),
    stale_after_move_recoverable=True,
)
