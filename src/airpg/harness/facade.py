"""Small public facade coordinating registered action handlers."""

from __future__ import annotations

from airpg.debug import DebugSink, NullDebugSink
from airpg.harness.actions.base import ActionContext
from airpg.harness.dialogue import build_dialogue_event, validate_dialogue
from airpg.harness.query import WorldQuery
from airpg.harness.registry import ACTION_HANDLERS
from airpg.models import EventKind, TurnIntent, WorldEvent, WorldState


class IntentRejected(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("；".join(reasons))


class WorldHarness:
    """The sole authority allowed to commit planned world mutations."""

    def __init__(self, state: WorldState, debug: DebugSink | None = None) -> None:
        self.state = state
        self.query = WorldQuery(state)
        self.world_log: list[WorldEvent] = []
        self.debug = debug or NullDebugSink()

    def validate(self, intent: TurnIntent) -> list[str]:
        if intent.actor_id not in self.state.actors:
            return [f"未知角色 {intent.actor_id!r}"]
        if intent.action is None and intent.dialogue is None:
            return ["本回合既没有动作也没有说话；如无事可做，请明确选择 wait"]
        reasons: list[str] = []
        if intent.dialogue is not None:
            reasons.extend(
                validate_dialogue(self.state, self.query, intent.actor_id, intent.dialogue)
            )
        if intent.action is not None:
            handler = ACTION_HANDLERS[intent.action.kind]
            reasons.extend(
                handler.validate(
                    ActionContext(intent.actor_id, self.state, self.query), intent.action
                )
            )
        return reasons

    def execute(self, intent: TurnIntent, round_number: int) -> list[WorldEvent]:
        reasons = self.validate(intent)
        if reasons:
            raise IntentRejected(reasons)

        events: list[WorldEvent] = []
        effect = None
        source_room_id = self.state.actors[intent.actor_id].room_id
        if intent.action is not None:
            handler = ACTION_HANDLERS[intent.action.kind]
            effect = handler.plan(
                ActionContext(intent.actor_id, self.state, self.query), intent.action
            )
            # Preconditions for every mutation are checked before any mutation is applied.
            effect.commit(self.state)

        if intent.dialogue is not None:
            event = build_dialogue_event(
                self.state,
                intent.actor_id,
                intent.dialogue,
                sequence=len(self.world_log) + 1,
                round_number=round_number,
                source_room_id=source_room_id,
            )
            self._append(event, events)

        if intent.action is not None:
            actor = self.state.actors[intent.actor_id]
            assert effect is not None
            event = WorldEvent(
                sequence=len(self.world_log) + 1,
                round_number=round_number,
                actor_id=intent.actor_id,
                kind=EventKind.ACTION,
                mode=intent.action.mode,
                action_kind=intent.action.kind,
                data={"source_room_id": source_room_id, **effect.data},
                detail_visibility=effect.detail_visibility,
                direct_observer_ids=effect.direct_observer_ids,
            )
            if effect.data:
                self.debug.emit(
                    "state_change",
                    f"{actor.name} 执行 {intent.action.kind.value}",
                    effect.data,
                )
            self._append(event, events)
        return events

    def _append(self, event: WorldEvent, turn_events: list[WorldEvent]) -> None:
        self.world_log.append(event)
        turn_events.append(event)
        self.debug.emit("world_event", f"世界事件 #{event.sequence}", event)
