"""Deterministic WORLD-authored reactions to action effect triggers."""

from __future__ import annotations

from token_odyssey.inside_act.domain.events import WorldEvent, WorldReactionEventData
from token_odyssey.inside_act.domain.spatial import WorldState


def render_world_event(state: WorldState, event: WorldEvent, *, full: bool) -> str:
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
