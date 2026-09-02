from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor, require_item
from token_odyssey.inside_act.actions.contracts import (
    ActionContext,
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
    PlacementMutation,
)
from token_odyssey.inside_act.domain.events import ActionEventData, ExecutionNotice, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class InstallIntent(BaseActionIntent):
    kind: Literal["install"] = "install"
    component_id: str
    target_entity_id: str


class InstallEventData(ActionEventData):
    component_id: str
    target_entity_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(InstallIntent, raw)
    component, reasons = require_item(context, intent.component_id)
    target, target_reasons = require_item(context, intent.target_entity_id)
    reasons.extend(target_reasons)
    placement = context.state.placements.get(intent.component_id)
    already_installed = (
        placement is not None
        and placement.parent_id == intent.target_entity_id
        and placement.relation == PlacementRelation.ATTACHED
    )
    if component is not None and not already_installed and not context.query.is_controlled_by(
        context.actor_id, component.id
    ):
        reasons.append(f"角色没有控制待安装组件 {component.name}")
    if target is not None and not context.query.is_accessible(context.actor_id, target.id):
        reasons.append(f"角色无法接触安装目标 {target.name}")
    if component is not None and target is not None and not context.state.mechanics.installation_allowed(
        component.id, target.id
    ):
        reasons.append(f"{component.name} 不能安装到 {target.name}")
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(InstallIntent, raw)
    old = context.state.placements[intent.component_id].model_copy(deep=True)
    data = InstallEventData(
        component_id=intent.component_id,
        target_entity_id=intent.target_entity_id,
    )
    if old.parent_id == intent.target_entity_id and old.relation == PlacementRelation.ATTACHED:
        return ActionEffect(
            data=data,
            emit_event=False,
            notices=[
                ExecutionNotice(
                    code="redundant_install",
                    message=(
                        f"install 未执行：{context.state.item(intent.component_id).name}"
                        f"已经安装在 {context.state.item(intent.target_entity_id).name} 上"
                    ),
                )
            ],
        )
    return ActionEffect(
        data=data,
        mutations=[
            PlacementMutation(
                intent.component_id,
                old,
                Placement(
                    relation=PlacementRelation.ATTACHED,
                    parent_id=intent.target_entity_id,
                ),
            )
        ],
        anchors=[actor_anchor(context.actor_id)],
        knowledge_entity_ids=[intent.component_id, intent.target_entity_id],
    )


def references(raw: BaseActionIntent) -> set[str]:
    intent = cast(InstallIntent, raw)
    return {intent.component_id, intent.target_entity_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return (
        f"{state.character(event.actor_id).name}把"
        f"「{state.item(event.data.component_id).name}」安装到"
        f"「{state.item(event.data.target_entity_id).name}」上。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    assert event.actor_id is not None
    return f"{state.character(event.actor_id).name}把一个部件接到了设备上。"


ACTION = ActionSpec(
    kind="install",
    intent_model=InstallIntent,
    event_model=InstallEventData,
    validate=validate,
    plan=plan,
    known_reference_extractor=references,
    intrinsic_visibility=0.7,
    render_full=render_full,
    render_partial=render_partial,
    prompt_usage="把控制中的组件安装到兼容设备上",
    prompt_requirements=("组件与设备已知", "自己控制组件", "设备可接触且接受该组件"),
    prompt_effect="组件变为 attached:设备，并被视为已安装",
    prompt_misuses=("不能用 place 代替 install",),
    stale_after_move_recoverable=True,
)
