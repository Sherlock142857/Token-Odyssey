"""Projection of canonical events into isolated, append-only observation streams."""

from __future__ import annotations

import random
from collections.abc import Callable
from copy import deepcopy

from airpg.debug import DebugSink, NullDebugSink
from airpg.harness.observation.renderers import render_event
from airpg.harness.spatial import SpatialRelationships
from airpg.models import (
    ActionKind,
    AgentRuntime,
    KnownItem,
    LocationKind,
    Observation,
    ObservationLevel,
    PerformanceMode,
    WorldEvent,
    WorldState,
)
from airpg.recording import NullRunRecorder


MODE_FACTORS = {
    PerformanceMode.NORMAL: 1.0,
    PerformanceMode.SECRETIVE: 0.3,
    PerformanceMode.CONSPICUOUS: 2.0,
}


class ObservationSystem:
    def __init__(
        self,
        state: WorldState,
        runtimes: dict[str, AgentRuntime],
        *,
        seed: int,
        debug: DebugSink | None = None,
        listeners: list[Callable[[Observation], None]] | None = None,
        recorder: object | None = None,
    ) -> None:
        self.state = state
        self.runtimes = runtimes
        self.spatial = SpatialRelationships(state)
        self.rng = random.Random(seed)
        self.debug = debug or NullDebugSink()
        self.listeners = listeners if listeners is not None else []
        self.recorder = recorder or NullRunRecorder()

    def scan_environment(
        self,
        actor_id: str,
        round_number: int,
        *,
        force_context_update: bool = False,
        reason: str = "你重新观察了周围环境。",
    ) -> list[Observation]:
        actor = self.state.actors[actor_id]
        runtime = self.runtimes[actor_id]
        produced: list[Observation] = []
        for other in self.state.actors.values():
            if other.room_id == actor.room_id:
                runtime.knowledge.known_actor_rooms[other.id] = other.room_id

        for known in runtime.knowledge.items.values():
            controlled = self._controlled_by(actor_id, known.item_id)
            present = self.spatial.item_room(known.item_id) == actor.room_id
            exposed = self.spatial.item_exposure(known.item_id) > 0.0
            if known.currently_visible and not controlled and (not present or not exposed):
                known.currently_visible = False
                produced.append(
                    self._add_observation(
                        actor_id,
                        ObservationLevel.FULL,
                        f"你现在看不到此前见过的「{known.name}」了；你不知道它为何消失。",
                        round_number,
                        is_system_update=True,
                    )
                )

        for item in self.state.items.values():
            if self.spatial.item_room(item.id) != actor.room_id:
                continue
            controlled = self._controlled_by(actor_id, item.id)
            full_score = 1.0 if controlled else self.spatial.item_exposure(item.id)
            known = runtime.knowledge.items.get(item.id)
            location_changed = known is not None and known.last_known_location != item.location
            if known is not None and known.currently_visible and not location_changed:
                continue
            roll = self.rng.random()
            partial_score = min(1.0, full_score * 1.5)
            roll_data = {
                "observer_id": actor_id,
                "item_id": item.id,
                "full_threshold": full_score,
                "partial_threshold": partial_score,
                "roll": roll,
            }
            self.debug.emit("observation_roll", f"{actor.name} 观察物品 {item.name}", roll_data)
            self.recorder.record_trace("item_observation_roll", roll_data)
            if controlled or roll <= full_score:
                runtime.knowledge.items[item.id] = self._known_item(item.id, currently_visible=True)
                if known is None:
                    text = f"你注意到可互动的物品「{item.name}」：{item.detailed_description}"
                elif location_changed:
                    text = f"你再次看到「{item.name}」，它的位置与记忆中不同。"
                else:
                    text = f"你再次看到了「{item.name}」。"
                produced.append(
                    self._add_observation(
                        actor_id,
                        ObservationLevel.FULL,
                        text,
                        round_number,
                        is_system_update=True,
                    )
                )
            elif roll <= partial_score:
                produced.append(
                    self._add_observation(
                        actor_id,
                        ObservationLevel.PARTIAL,
                        "你隐约觉得附近有个可互动的东西，但没能辨认清楚。",
                        round_number,
                        is_system_update=True,
                    )
                )

        if force_context_update:
            visible_names = [
                known.name for known in runtime.knowledge.items.values() if known.currently_visible
            ]
            colocated_names = [
                other.name
                for other in self.state.actors.values()
                if other.id != actor_id and other.room_id == actor.room_id
            ]
            room = self.state.rooms[actor.room_id]
            produced.append(
                self._add_observation(
                    actor_id,
                    ObservationLevel.FULL,
                    f"{reason} 你现在位于「{room.name}」。"
                    f"当前明确可互动的物品：{'、'.join(visible_names) if visible_names else '无'}；"
                    f"同处角色：{'、'.join(colocated_names) if colocated_names else '无'}。",
                    round_number,
                    is_system_update=True,
                )
            )
        return produced

    def reveal_container(
        self, actor_id: str, container_id: str, round_number: int
    ) -> list[Observation]:
        runtime = self.runtimes[actor_id]
        container = self.state.items[container_id]
        children = [
            item
            for item in self.state.items.values()
            if item.location.kind == LocationKind.CONTAINER
            and item.location.target_id == container_id
        ]
        if not children:
            return [
                self._add_observation(
                    actor_id,
                    ObservationLevel.FULL,
                    f"你搜索了「{container.name}」，里面没有物品。",
                    round_number,
                    is_system_update=True,
                )
            ]
        for item in children:
            runtime.knowledge.items[item.id] = self._known_item(item.id, currently_visible=True)
        names = "、".join(f"「{item.name}」" for item in children)
        return [
            self._add_observation(
                actor_id,
                ObservationLevel.FULL,
                f"你搜索了「{container.name}」，确认其中有：{names}。",
                round_number,
                is_system_update=True,
            )
        ]

    def project_event(self, event: WorldEvent) -> dict[str, Observation | None]:
        projected: dict[str, Observation | None] = {}
        source_rooms = self._event_source_rooms(event)
        for observer_id, observer in self.state.actors.items():
            if observer_id == event.actor_id or observer_id in event.direct_observer_ids:
                level = ObservationLevel.FULL
                roll = None
                full_score = partial_score = 1.0
            else:
                spatial = max(
                    self.spatial.visibility(observer.room_id, source_room)
                    for source_room in source_rooms
                )
                salience = spatial * MODE_FACTORS[event.mode]
                full_score = min(1.0, salience * event.detail_visibility)
                partial_score = min(1.0, salience * 1.5)
                roll = self.rng.random()
                if roll <= full_score:
                    level = ObservationLevel.FULL
                elif roll <= partial_score:
                    level = ObservationLevel.PARTIAL
                else:
                    projected[observer_id] = None
                    payload = {
                        "observer_id": observer_id,
                        "event_sequence": event.sequence,
                        "full_threshold": full_score,
                        "partial_threshold": partial_score,
                        "roll": roll,
                    }
                    self.debug.emit(
                        "observation_roll",
                        f"{observer.name} 未观察到事件 #{event.sequence}",
                        payload,
                    )
                    self.recorder.record_projection(outcome="none", **payload)
                    continue

            observation = self._add_observation(
                observer_id,
                level,
                self.render_event(event, level),
                event.round_number,
                source_event_sequence=event.sequence,
            )
            projected[observer_id] = observation
            if level == ObservationLevel.FULL:
                self._update_knowledge_from_event(observer_id, event)
            payload = {
                "observer_id": observer_id,
                "event_sequence": event.sequence,
                "outcome": level.value,
                "full_threshold": full_score,
                "partial_threshold": partial_score,
                "roll": roll,
            }
            self.debug.emit(
                "observation",
                f"{observer.name} 对事件 #{event.sequence} 的投影：{level.value}",
                {"observation": observation, **payload},
            )
            self.recorder.record_trace("event_projection", payload)
        return projected

    def render_event(self, event: WorldEvent, level: ObservationLevel) -> str:
        return render_event(self.state, event, level)

    def _event_source_rooms(self, event: WorldEvent) -> set[str]:
        if event.action_kind == ActionKind.MOVE:
            return {str(event.data["from_room_id"]), str(event.data["to_room_id"])}
        source_room_id = event.data.get("source_room_id")
        if isinstance(source_room_id, str):
            return {source_room_id}
        return {self.state.actors[event.actor_id].room_id}

    def _update_knowledge_from_event(self, observer_id: str, event: WorldEvent) -> None:
        runtime = self.runtimes[observer_id]
        if event.action_kind == ActionKind.MOVE:
            runtime.knowledge.known_actor_rooms[event.actor_id] = str(event.data["to_room_id"])
        item_id = event.data.get("item_id")
        if isinstance(item_id, str) and item_id in self.state.items:
            observer_room = self.state.actors[observer_id].room_id
            currently_visible = self._controlled_by(observer_id, item_id) or (
                self.spatial.item_room(item_id) == observer_room
                and self.spatial.item_exposure(item_id) >= 0.1
            )
            runtime.knowledge.items[item_id] = self._known_item(
                item_id, currently_visible=currently_visible
            )

    def _known_item(self, item_id: str, *, currently_visible: bool) -> KnownItem:
        item = self.state.items[item_id]
        return KnownItem(
            item_id=item.id,
            name=item.name,
            description=item.detailed_description,
            last_known_location=deepcopy(item.location),
            currently_visible=currently_visible,
        )

    def _controlled_by(self, actor_id: str, item_id: str) -> bool:
        location = self.state.items[item_id].location
        return location.kind in {LocationKind.HELD, LocationKind.HIDDEN, LocationKind.ATTACHED} and (
            location.target_id == actor_id
        )

    def _add_observation(
        self,
        observer_id: str,
        level: ObservationLevel,
        text: str,
        round_number: int,
        *,
        source_event_sequence: int | None = None,
        is_system_update: bool = False,
    ) -> Observation:
        observation = Observation(
            observer_id=observer_id,
            level=level,
            text=text,
            round_number=round_number,
            source_event_sequence=source_event_sequence,
            is_system_update=is_system_update,
        )
        self.runtimes[observer_id].observations.append(observation)
        for listener in self.listeners:
            listener(observation)
        if is_system_update:
            self.debug.emit("context_update", f"{observer_id} 收到私有环境更新", observation)
        return observation

