"""Seeded observation policy, knowledge projection, and objective rendering dispatch."""

from __future__ import annotations

import random
from collections.abc import Callable
from copy import deepcopy

from token_odyssey.inside_act.actions.registry import ActionRegistry
from token_odyssey.inside_act.context import (
    EntityView,
    EnvironmentProjection,
    ScanObservationStatus,
)
from token_odyssey.inside_act.domain.entities import EntityKind, Item
from token_odyssey.inside_act.domain.events import (
    AnchorSnapshot,
    CommittedFrame,
    KnowledgeGrantDirective,
    ObservationDirective,
    ScanEnvironmentDirective,
    WorldEvent,
)
from token_odyssey.inside_act.domain.knowledge import (
    AgentRuntime,
    KnownEntity,
    Observation,
    ObservationLevel,
)
from token_odyssey.inside_act.domain.spatial import WorldState
from token_odyssey.inside_act.visibility import VisibilityService
from token_odyssey.inside_act.mechanics import render_world_event


class ObservationPolicy:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def decide(
        self, score: float, partial_factor: float
    ) -> tuple[ObservationLevel | None, float]:
        roll = self.rng.random()
        if score > 0.0 and roll <= score:
            return ObservationLevel.FULL, roll
        if score > 0.0 and roll <= min(1.0, score * partial_factor):
            return ObservationLevel.PARTIAL, roll
        return None, roll


class ObservationSystem:
    def __init__(
        self,
        runtimes: dict[str, AgentRuntime],
        registry: ActionRegistry,
        *,
        seed: int,
        listeners: list[Callable[[Observation], None]] | None = None,
        trace_listener: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.runtimes = runtimes
        self.registry = registry
        self.visibility = VisibilityService()
        self.policy = ObservationPolicy(seed)
        self.listeners = listeners if listeners is not None else []
        self.trace_listener = trace_listener

    def scan_environment(
        self, state: WorldState, observer_id: str, round_number: int
    ) -> EnvironmentProjection:
        runtime = self.runtimes[observer_id]
        for known in runtime.knowledge.entities.values():
            known.currently_observable = False

        full_observations: list[EntityView] = []
        for entity_id, entity in state.entities.items():
            if entity_id == observer_id or entity.kind == EntityKind.ROOM:
                continue
            score = self.visibility.base_visibility(state, observer_id, entity_id)
            if isinstance(entity, Item):
                score *= entity.intrinsic_visibility
            placement = state.placements.get(entity_id)
            direct_control = placement is not None and placement.parent_id == observer_id
            if direct_control:
                level, roll = ObservationLevel.FULL, None
            else:
                level, roll = self.policy.decide(
                    min(1.0, score), state.rules.partial_visibility_factor
                )
            self._trace(
                "environment_projection",
                {
                    "observer_id": observer_id,
                    "entity_id": entity_id,
                    "score": min(1.0, score),
                    "roll": roll,
                    "outcome": level.value if level else "none",
                },
            )
            if level == ObservationLevel.FULL:
                old = runtime.knowledge.entities.get(entity_id)
                old_placement = old.last_observed_placement if old is not None else None
                if old is None:
                    observation_status = ScanObservationStatus.NEW
                elif old_placement != placement:
                    observation_status = ScanObservationStatus.MOVED
                else:
                    observation_status = ScanObservationStatus.UNCHANGED
                self._remember(state, observer_id, entity_id, round_number, observable=True)
                view = self._view(state, entity_id).model_copy(
                    update={
                        "change": observation_status,
                    }
                )
                full_observations.append(view)
            elif level == ObservationLevel.PARTIAL:
                self._add_observation(
                    observer_id,
                    ObservationLevel.PARTIAL,
                    "附近有一处轮廓或移动，但没有辨清对象。",
                    round_number,
                    is_system_update=True,
                )
        return EnvironmentProjection(full_observations=full_observations)

    def project_frame(
        self, frame: CommittedFrame
    ) -> dict[int, dict[str, Observation | None]]:
        projected: dict[int, dict[str, Observation | None]] = {}
        for event in frame.events:
            event_projection: dict[str, Observation | None] = {}
            for observer_id in frame.after_state.character_ids:
                if event.source.value == "action" and observer_id == event.actor_id:
                    self._trace(
                        "event_projection",
                        {
                            "observer_id": observer_id,
                            "event_sequence": event.sequence,
                            "score": 1.0,
                            "roll": None,
                            "outcome": "self_action_suppressed",
                        },
                    )
                    event_projection[observer_id] = None
                    continue
                if observer_id in event.guaranteed_observer_ids:
                    level = ObservationLevel.FULL
                    roll = None
                    score = 1.0
                else:
                    anchor_scores: list[float] = []
                    for anchor in event.anchors:
                        anchor_state = (
                            frame.before_state
                            if anchor.snapshot == AnchorSnapshot.BEFORE
                            else frame.after_state
                        )
                        if observer_id in anchor_state.entities and anchor.entity_id in anchor_state.entities:
                            anchor_scores.append(
                                self.visibility.base_visibility(
                                    anchor_state, observer_id, anchor.entity_id
                                )
                            )
                    base = max(anchor_scores, default=0.0)
                    score = min(
                        1.0,
                        base * event.intrinsic_visibility * event.amplitude.factor,
                    )
                    level, roll = self.policy.decide(
                        score, frame.after_state.rules.partial_visibility_factor
                    )
                self._trace(
                    "event_projection",
                    {
                        "observer_id": observer_id,
                        "event_sequence": event.sequence,
                        "score": score,
                        "roll": roll,
                        "outcome": level.value if level else "none",
                    },
                )
                if level is None:
                    event_projection[observer_id] = None
                    continue
                full = level == ObservationLevel.FULL
                if event.source.value == "world":
                    text = render_world_event(frame.after_state, event, full=full)
                else:
                    text = self.registry.render(frame.after_state, event, full=full)
                newly_known = []
                if full:
                    runtime = self.runtimes[observer_id]
                    newly_known = [
                        entity_id
                        for entity_id in event.knowledge_entity_ids
                        if entity_id in frame.after_state.entities
                        and entity_id not in runtime.knowledge.entities
                    ]
                    if newly_known:
                        text += _first_full_details(frame.after_state, newly_known)
                observation = self._add_observation(
                    observer_id,
                    level,
                    text,
                    event.round_number,
                    source_event_sequence=event.sequence,
                )
                event_projection[observer_id] = observation
                if full:
                    for entity_id in event.knowledge_entity_ids:
                        if entity_id in frame.after_state.entities:
                            self._remember(
                                frame.after_state,
                                observer_id,
                                entity_id,
                                event.round_number,
                                observable=self._currently_observable(
                                    frame.after_state, observer_id, entity_id
                                ),
                            )
            projected[event.sequence] = event_projection
        return projected

    def apply_directives(
        self,
        state: WorldState,
        directives: tuple[ObservationDirective, ...],
        round_number: int,
    ) -> None:
        for directive in directives:
            if isinstance(directive, ScanEnvironmentDirective):
                self.scan_environment(state, directive.observer_id, round_number)
            elif isinstance(directive, KnowledgeGrantDirective):
                self._grant_knowledge(
                    state,
                    directive.observer_id,
                    directive.entity_ids,
                    directive.text,
                    round_number,
                )

    def _grant_knowledge(
        self,
        state: WorldState,
        observer_id: str,
        entity_ids: list[str],
        text: str,
        round_number: int,
    ) -> None:
        runtime = self.runtimes[observer_id]
        newly_known = [
            entity_id
            for entity_id in entity_ids
            if entity_id in state.entities and entity_id not in runtime.knowledge.entities
        ]
        for entity_id in entity_ids:
            self._remember(state, observer_id, entity_id, round_number, observable=True)
        if newly_known:
            text += _first_full_details(state, newly_known)
        self._add_observation(
            observer_id,
            ObservationLevel.FULL,
            text,
            round_number,
            is_system_update=True,
        )

    def _currently_observable(
        self, state: WorldState, observer_id: str, entity_id: str
    ) -> bool:
        entity = state.entities[entity_id]
        score = self.visibility.base_visibility(state, observer_id, entity_id)
        if isinstance(entity, Item):
            score *= entity.intrinsic_visibility
        return score > 0.0

    def _remember(
        self,
        state: WorldState,
        observer_id: str,
        entity_id: str,
        round_number: int,
        *,
        observable: bool,
    ) -> None:
        runtime = self.runtimes[observer_id]
        entity = state.entities[entity_id]
        old = runtime.knowledge.entities.get(entity_id)
        runtime.knowledge.entities[entity_id] = KnownEntity(
            entity_id=entity_id,
            kind=entity.kind,
            name=entity.name,
            description=entity.description,
            last_observed_placement=deepcopy(state.placements.get(entity_id)),
            first_observed_round=(old.first_observed_round if old else round_number),
            last_observed_round=round_number,
            currently_observable=observable,
        )

    @staticmethod
    def _view(state: WorldState, entity_id: str) -> EntityView:
        entity = state.entities[entity_id]
        return EntityView(
            id=entity_id,
            name=entity.name,
            description=entity.description,
            placement=deepcopy(state.placements.get(entity_id)),
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
        return observation

    def add_system_observation(
        self,
        observer_id: str,
        text: str,
        round_number: int,
    ) -> Observation:
        return self._add_observation(
            observer_id,
            ObservationLevel.FULL,
            text,
            round_number,
            is_system_update=True,
        )

    def _trace(self, category: str, payload: dict) -> None:
        if self.trace_listener is not None:
            self.trace_listener(category, payload)


def _first_full_details(state: WorldState, entity_ids: list[str]) -> str:
    details = [
        f"「{state.entities[entity_id].name}」：{state.entities[entity_id].description}"
        for entity_id in entity_ids
        if state.entities[entity_id].description
    ]
    return ("\n首次看清：" + "；".join(details)) if details else ""
