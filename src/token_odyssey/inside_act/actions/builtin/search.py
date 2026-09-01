from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import ActionContext, ActionEffect, ActionSpec, BaseActionIntent
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor
from token_odyssey.inside_act.domain.events import ActionEventData, KnowledgeGrantDirective, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class SearchIntent(BaseActionIntent):
    kind: Literal["search"] = "search"
    target_entity_id: str


class SearchEventData(ActionEventData):
    container_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(SearchIntent, raw)
    target = context.state.entities.get(intent.target_entity_id)
    if target is None:
        return [f"未知实体 {intent.target_entity_id!r}"]
    reasons: list[str] = []
    if not target.is_container:
        reasons.append(f"{target.name} 不是容器")
    elif not context.query.is_accessible(context.actor_id, target.id):
        reasons.append(f"角色无法接触容器 {target.name}")
    return reasons


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(SearchIntent, raw)
    children = context.state.children_of(intent.target_entity_id)
    container = context.state.entities[intent.target_entity_id]
    if children:
        names = "、".join(f"「{context.state.entities[child].name}」" for child in children)
        result_text = f"你检查了「{container.name}」内部，确认其中有：{names}。"
    else:
        result_text = f"你检查了「{container.name}」内部，其中没有物品。"
    return ActionEffect(
        data=SearchEventData(container_id=intent.target_entity_id),
        anchors=[actor_anchor(context.actor_id)],
        directives=[
            KnowledgeGrantDirective(
                observer_id=context.actor_id,
                entity_ids=children,
                text=result_text,
            )
        ],
    )


def references(raw: BaseActionIntent) -> set[str]:
    return {cast(SearchIntent, raw).target_entity_id}


def render_full(state: WorldState, event: WorldEvent) -> str:
    return (
        f"{state.character(event.actor_id).name}搜索了"
        f"「{state.entities[event.data.container_id].name}」。"
    )


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}翻查身边的一处位置。"


ACTION = ActionSpec(
    kind="search", intent_model=SearchIntent, event_model=SearchEventData, validate=validate, plan=plan,
    known_reference_extractor=references, intrinsic_visibility=0.7,
    render_full=render_full, render_partial=render_partial,
    prompt_usage="搜索一个已知容器",
    prompt_requirements=("目标已知且 is_container=true", "目标可接触",),
    prompt_effect="确认容器直接子物品，并把这些实体加入自己的知识",
    prompt_misuses=("同 frame 的其他命令不能依赖搜索结果；请放在后续 frame",),
)
