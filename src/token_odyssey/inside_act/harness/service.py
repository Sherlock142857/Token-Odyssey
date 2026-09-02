"""Atomic resolution of registered commands against draft world snapshots."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from pydantic import ValidationError

from token_odyssey.inside_act.actions.contracts import (
    ActionContext,
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
    TurnPlan,
)
from token_odyssey.inside_act.actions.query import WorldQuery
from token_odyssey.inside_act.actions.registry import ActionRegistry
from token_odyssey.inside_act.domain.events import (
    AcceptedTurn,
    CommittedFrame,
    KnowledgeGrantDirective,
    RejectedTurn,
    Resolution,
    ValidationIssue,
    WorldEvent,
)
from token_odyssey.inside_act.domain.spatial import WorldState


@dataclass(frozen=True)
class _PlannedCommand:
    command: BaseActionIntent
    spec: ActionSpec
    effect: ActionEffect


@dataclass(frozen=True)
class _PlannedFrame:
    index: int
    before_state: WorldState
    after_state: WorldState
    commands: tuple[_PlannedCommand, ...]


class WorldHarness:
    """The sole owner allowed to replace the canonical WorldState."""

    def __init__(self, state: WorldState, registry: ActionRegistry) -> None:
        self._state = state.model_copy(deep=True)
        self.registry = registry
        self._world_log: list[WorldEvent] = []

    @property
    def state(self) -> WorldState:
        return self._state.model_copy(deep=True)

    @property
    def world_log(self) -> list[WorldEvent]:
        return [event.model_copy(deep=True) for event in self._world_log]

    def validate(
        self,
        actor_id: str,
        plan: TurnPlan,
        *,
        known_entity_ids: Collection[str] | None = None,
    ) -> tuple[ValidationIssue, ...]:
        resolution = self._plan(actor_id, plan, known_entity_ids=known_entity_ids)
        if isinstance(resolution, RejectedTurn):
            return resolution.validation_issues
        return ()

    def resolve(
        self,
        actor_id: str,
        plan: TurnPlan,
        round_number: int,
        *,
        known_entity_ids: Collection[str] | None = None,
    ) -> Resolution:
        planned = self._plan(actor_id, plan, known_entity_ids=known_entity_ids)
        if isinstance(planned, RejectedTurn):
            return planned

        assert isinstance(planned, tuple)
        final_state = planned[-1].after_state.model_copy(deep=True)
        final_state.revision = self._state.revision + 1
        sequence = len(self._world_log)
        committed_frames: list[CommittedFrame] = []
        all_events: list[WorldEvent] = []
        all_directives = []
        for planned_index, frame in enumerate(planned):
            before_state = frame.before_state.model_copy(deep=True)
            after_state = frame.after_state.model_copy(deep=True)
            if planned_index == len(planned) - 1:
                after_state.revision = final_state.revision
            events: list[WorldEvent] = []
            directives = []
            for command_plan in frame.commands:
                effect = command_plan.effect
                if not effect.emit_event:
                    continue
                sequence += 1
                event = WorldEvent(
                    sequence=sequence,
                    round_number=round_number,
                    frame_index=frame.index,
                    actor_id=actor_id,
                    action_kind=command_plan.command.kind,
                    amplitude=command_plan.command.amplitude,
                    data=effect.data,
                    intrinsic_visibility=command_plan.spec.intrinsic_visibility,
                    anchors=effect.anchors,
                    guaranteed_observer_ids=list(
                        dict.fromkeys([actor_id, *effect.guaranteed_observer_ids])
                    ),
                    knowledge_entity_ids=list(dict.fromkeys(effect.knowledge_entity_ids)),
                )
                events.append(event)
                all_events.append(event)
                directives.extend(effect.directives)
                all_directives.extend(effect.directives)
            committed_frames.append(
                CommittedFrame(
                    index=frame.index,
                    before_state=before_state,
                    after_state=after_state,
                    events=tuple(events),
                    directives=tuple(directives),
                )
            )

        self._state = final_state.model_copy(deep=True)
        self._world_log.extend(event.model_copy(deep=True) for event in all_events)
        return AcceptedTurn(
            committed_frames=tuple(committed_frames),
            final_state=final_state.model_copy(deep=True),
            events=tuple(all_events),
            observation_directives=tuple(all_directives),
        )

    def _plan(
        self,
        actor_id: str,
        plan: TurnPlan,
        *,
        known_entity_ids: Collection[str] | None,
    ) -> tuple[_PlannedFrame, ...] | RejectedTurn:
        if actor_id not in self._state.character_ids:
            return self._reject("unknown_actor", f"未知角色 {actor_id!r}")
        shape_reasons = self.registry.validate_shape(plan)
        if shape_reasons:
            return RejectedTurn(
                tuple(ValidationIssue(code="turn_shape", message=reason) for reason in shape_reasons)
            )

        draft = self._state.model_copy(deep=True)
        known = set(known_entity_ids) if known_entity_ids is not None else None
        planned_frames: list[_PlannedFrame] = []
        for frame_index, frame in enumerate(plan.frames):
            before = draft.model_copy(deep=True)
            query = WorldQuery(before)
            command_plans: list[_PlannedCommand] = []
            issues: list[ValidationIssue] = []
            if known is not None:
                for command_index, command in enumerate(frame.commands):
                    unknown = sorted(self.registry.known_references(command) - known)
                    issues.extend(
                        ValidationIssue(
                            code="unknown_to_actor",
                            message=(
                                f"{command.kind} 引用了你尚未确认的实体 {entity_id!r}；"
                                "只能引用此前已知实体，或放到产生该知识的后续 frame"
                            ),
                            frame_index=frame_index,
                            command_index=command_index,
                        )
                        for entity_id in unknown
                    )
            if issues:
                return RejectedTurn(tuple(issues))

            for command_index, command in enumerate(frame.commands):
                spec = self.registry.spec(command.kind)
                reasons = spec.validate(ActionContext(actor_id, before, query), command)
                issues.extend(
                    ValidationIssue(
                        code="action_invalid",
                        message=reason,
                        frame_index=frame_index,
                        command_index=command_index,
                    )
                    for reason in reasons
                )
                if not reasons:
                    effect = spec.plan(ActionContext(actor_id, before, query), command)
                    if not isinstance(effect.data, spec.event_model):
                        raise TypeError(
                            f"Action {spec.kind!r} planned {type(effect.data).__name__}, "
                            f"expected {spec.event_model.__name__}"
                        )
                    command_plans.append(_PlannedCommand(command, spec, effect))
            if issues:
                return RejectedTurn(tuple(issues))

            mutation_owners: dict[str, int] = {}
            for command_index, command_plan in enumerate(command_plans):
                for mutation in command_plan.effect.mutations:
                    entity_id = getattr(mutation, "entity_id", None)
                    if entity_id is None:
                        continue
                    previous = mutation_owners.get(entity_id)
                    if previous is not None:
                        return RejectedTurn(
                            (
                                ValidationIssue(
                                    code="simultaneous_conflict",
                                    message=(
                                        f"同一 frame 的 command {previous + 1} 和 "
                                        f"command {command_index + 1} 都会改变实体 {entity_id!r}；"
                                        "请把有先后关系的动作拆到不同 frame"
                                    ),
                                    frame_index=frame_index,
                                    command_index=command_index,
                                ),
                            )
                        )
                    mutation_owners[entity_id] = command_index

            after = before.model_copy(deep=True)
            try:
                for command_plan in command_plans:
                    for mutation in command_plan.effect.mutations:
                        mutation.apply(after)
                after = WorldState.model_validate(after.model_dump(mode="python"))
            except (RuntimeError, ValidationError, ValueError) as exc:
                detail = _world_error_message(exc)
                return RejectedTurn(
                    (
                        ValidationIssue(
                            code="world_invariant",
                            message=(
                                f"这个 frame 的组合效果破坏了空间不变量：{detail}；"
                                "请移除冲突动作或拆到不同 frame"
                            ),
                            frame_index=frame_index,
                        ),
                    )
                )
            planned_frames.append(
                _PlannedFrame(frame_index, before, after, tuple(command_plans))
            )
            if known is not None:
                for command_plan in command_plans:
                    known.update(command_plan.effect.knowledge_entity_ids)
                    for directive in command_plan.effect.directives:
                        if (
                            isinstance(directive, KnowledgeGrantDirective)
                            and directive.observer_id == actor_id
                        ):
                            known.update(directive.entity_ids)
            draft = after
        return tuple(planned_frames)

    @staticmethod
    def _reject(code: str, message: str) -> RejectedTurn:
        return RejectedTurn((ValidationIssue(code=code, message=message),))


def _world_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_input=False)
        if errors:
            message = str(errors[0].get("msg", "状态无效"))
            return message.removeprefix("Value error, ")
    return str(exc)
