"""Atomic resolution of registered commands against draft world snapshots."""

from __future__ import annotations

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
        self.world_log: list[WorldEvent] = []

    @property
    def state(self) -> WorldState:
        return self._state

    def validate(self, actor_id: str, plan: TurnPlan) -> tuple[ValidationIssue, ...]:
        resolution = self._plan(actor_id, plan)
        if isinstance(resolution, RejectedTurn):
            return resolution.validation_issues
        return ()

    def resolve(self, actor_id: str, plan: TurnPlan, round_number: int) -> Resolution:
        planned = self._plan(actor_id, plan)
        if isinstance(planned, RejectedTurn):
            return planned

        assert isinstance(planned, tuple)
        final_state = planned[-1].after_state
        final_state.revision = self._state.revision + 1
        sequence = len(self.world_log)
        committed_frames: list[CommittedFrame] = []
        all_events: list[WorldEvent] = []
        all_directives = []
        for frame in planned:
            events: list[WorldEvent] = []
            directives = []
            for command_plan in frame.commands:
                sequence += 1
                effect = command_plan.effect
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
                    before_state=frame.before_state,
                    after_state=frame.after_state,
                    events=tuple(events),
                    directives=tuple(directives),
                )
            )

        self._state = final_state
        self.world_log.extend(all_events)
        return AcceptedTurn(
            committed_frames=tuple(committed_frames),
            final_state=final_state,
            events=tuple(all_events),
            observation_directives=tuple(all_directives),
        )

    def _plan(
        self, actor_id: str, plan: TurnPlan
    ) -> tuple[_PlannedFrame, ...] | RejectedTurn:
        if actor_id not in self._state.character_ids:
            return self._reject("unknown_actor", f"未知角色 {actor_id!r}")
        shape_reasons = self.registry.validate_shape(plan)
        if shape_reasons:
            return RejectedTurn(
                tuple(ValidationIssue(code="turn_shape", message=reason) for reason in shape_reasons)
            )

        draft = self._state.model_copy(deep=True)
        planned_frames: list[_PlannedFrame] = []
        for frame_index, frame in enumerate(plan.frames):
            before = draft.model_copy(deep=True)
            query = WorldQuery(before)
            command_plans: list[_PlannedCommand] = []
            issues: list[ValidationIssue] = []
            for command_index, command in enumerate(frame.commands):
                try:
                    spec = self.registry.spec(command.kind)
                    reasons = spec.validate(ActionContext(actor_id, before, query), command)
                except Exception as exc:
                    reasons = [f"动作校验失败：{exc}"]
                    spec = self.registry.spec(command.kind)
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
                    try:
                        effect = spec.plan(ActionContext(actor_id, before, query), command)
                        if not isinstance(effect.data, spec.event_model):
                            raise TypeError(
                                f"Action {spec.kind!r} planned {type(effect.data).__name__}, "
                                f"expected {spec.event_model.__name__}"
                            )
                        command_plans.append(_PlannedCommand(command, spec, effect))
                    except Exception as exc:
                        issues.append(
                            ValidationIssue(
                                code="action_planning_failed",
                                message=str(exc),
                                frame_index=frame_index,
                                command_index=command_index,
                            )
                        )
            if issues:
                return RejectedTurn(tuple(issues))

            after = before.model_copy(deep=True)
            try:
                for command_plan in command_plans:
                    for mutation in command_plan.effect.mutations:
                        mutation.apply(after)
                after = WorldState.model_validate(after.model_dump(mode="python"))
            except (RuntimeError, ValidationError, ValueError) as exc:
                return RejectedTurn(
                    (
                        ValidationIssue(
                            code="world_invariant",
                            message=str(exc),
                            frame_index=frame_index,
                        ),
                    )
                )
            planned_frames.append(
                _PlannedFrame(frame_index, before, after, tuple(command_plans))
            )
            draft = after
        return tuple(planned_frames)

    @staticmethod
    def _reject(code: str, message: str) -> RejectedTurn:
        return RejectedTurn((ValidationIssue(code=code, message=message),))
