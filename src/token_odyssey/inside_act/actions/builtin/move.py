from __future__ import annotations

from typing import Literal, cast

from token_odyssey.inside_act.actions.contracts import (
    ActionContext,
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
    PlacementMutation,
)
from token_odyssey.inside_act.actions.builtin.helpers import actor_anchor
from token_odyssey.inside_act.domain.entities import Room
from token_odyssey.inside_act.domain.events import ActionEventData, ScanEnvironmentDirective, WorldEvent
from token_odyssey.inside_act.domain.spatial import Placement, PlacementRelation, WorldState


class MoveIntent(BaseActionIntent):
    kind: Literal["move"] = "move"
    destination_room_id: str


class MoveEventData(ActionEventData):
    from_room_id: str
    to_room_id: str


def validate(context: ActionContext, raw: BaseActionIntent) -> list[str]:
    intent = cast(MoveIntent, raw)
    destination = context.state.entities.get(intent.destination_room_id)
    if not isinstance(destination, Room):
        return [f"不存在房间 {intent.destination_room_id!r}"]
    if context.state.root_room_of(context.actor_id) == intent.destination_room_id:
        return ["角色已经在目标房间"]
    return []


def plan(context: ActionContext, raw: BaseActionIntent) -> ActionEffect:
    intent = cast(MoveIntent, raw)
    old = context.state.placements[context.actor_id].model_copy(deep=True)
    new = Placement(relation=PlacementRelation.INSIDE, parent_id=intent.destination_room_id)
    return ActionEffect(
        data=MoveEventData(
            from_room_id=context.state.root_room_of(context.actor_id),
            to_room_id=intent.destination_room_id,
        ),
        mutations=[PlacementMutation(context.actor_id, old, new)],
        anchors=[actor_anchor(context.actor_id), actor_anchor(context.actor_id, after=True)],
        knowledge_entity_ids=[context.actor_id],
        directives=[
            ScanEnvironmentDirective(
                observer_id=context.actor_id,
                reason="移动完成后重新观察环境。",
            )
        ],
    )


def render_full(state: WorldState, event: WorldEvent) -> str:
    actor = state.character(event.actor_id)
    old_room = state.room(event.data.from_room_id)
    new_room = state.room(event.data.to_room_id)
    return f"{actor.name}从「{old_room.name}」移动到「{new_room.name}」。"


def render_partial(state: WorldState, event: WorldEvent) -> str:
    return f"你看见{state.character(event.actor_id).name}离开了原来的位置。"


ACTION = ActionSpec(
    kind="move",
    intent_model=MoveIntent,
    event_model=MoveEventData,
    validate=validate,
    plan=plan,
    known_reference_extractor=lambda _: set(),
    intrinsic_visibility=1.0,
    render_full=render_full,
    render_partial=render_partial,
    prompt_usage="移动到一个 Room",
)
