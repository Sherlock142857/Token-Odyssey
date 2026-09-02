"""Deterministic WORLD-authored reactions to action effect triggers."""

from __future__ import annotations

from token_odyssey.inside_act.domain.events import (
    WorldEvent,
    WorldNoEffectEventData,
    WorldReactionEventData,
)
from token_odyssey.inside_act.domain.spatial import WorldState


def render_world_event(state: WorldState, event: WorldEvent, *, full: bool) -> str:
    if isinstance(event.data, WorldNoEffectEventData):
        if full:
            return f"对「{state.item(event.source_entity_id).name}」的操作没有产生任何效果。"
        return "附近传来操作物品的动静，但没有出现明显变化。"
    if event.mechanic_id is None:
        raise ValueError("WORLD event is missing mechanic_id")
    operation = next(
        (rule for rule in state.mechanics.operations if rule.id == event.mechanic_id),
        None,
    )
    if operation is None:
        raise ValueError(f"unknown WORLD mechanic {event.mechanic_id!r}")
    if not isinstance(event.data, WorldReactionEventData):
        raise TypeError("WORLD reaction has unexpected event data")
    reaction = operation.success if event.data.outcome == "success" else operation.failure
    return reaction.full_text if full else reaction.partial_text
