"""Dialogue is independent from physical actions and can coexist with one."""

from airpg.harness.query import WorldQuery
from airpg.models import DialogueIntent, EventKind, WorldEvent, WorldState


def validate_dialogue(
    state: WorldState, query: WorldQuery, actor_id: str, dialogue: DialogueIntent
) -> list[str]:
    return query.same_room_target_reasons(
        actor_id, dialogue.target_actor_ids, allow_self=False
    )


def build_dialogue_event(
    state: WorldState,
    actor_id: str,
    dialogue: DialogueIntent,
    *,
    sequence: int,
    round_number: int,
    source_room_id: str | None = None,
) -> WorldEvent:
    return WorldEvent(
        sequence=sequence,
        round_number=round_number,
        actor_id=actor_id,
        kind=EventKind.DIALOGUE,
        mode=dialogue.mode,
        data={
            "source_room_id": source_room_id or state.actors[actor_id].room_id,
            "target_actor_ids": list(dict.fromkeys(dialogue.target_actor_ids)),
            "content": dialogue.content,
        },
        direct_observer_ids=list(dict.fromkeys(dialogue.target_actor_ids)),
    )
