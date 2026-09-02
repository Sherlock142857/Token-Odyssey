"""Complete within-Act orchestration without Action- or provider-specific branches."""

from __future__ import annotations

from dataclasses import dataclass

from token_odyssey.agents.contracts import (
    AgentDecision,
    AgentError,
    AgentUnavailableError,
    DecisionRequest,
    Participant,
    ValidationFeedback,
)
from token_odyssey.inside_act.actions.contracts import TurnPlan
from token_odyssey.inside_act.actions.registry import ActionRegistry
from token_odyssey.inside_act.context import ContextProjector
from token_odyssey.inside_act.domain.events import (
    AcceptedTurn,
    EventSource,
    ExecutionNotice,
    RejectedTurn,
    ValidationIssue,
    WorldEvent,
)
from token_odyssey.inside_act.domain.knowledge import AgentRuntime
from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.inside_act.harness import WorldHarness
from token_odyssey.inside_act.observation import ObservationSystem
from token_odyssey.inside_act.mechanics import render_world_event
from token_odyssey.inside_act.router.contracts import TurnRouter
from token_odyssey.recording import NullRunRecorder


@dataclass(frozen=True)
class RunResult:
    rounds_completed: int
    turns_completed: int
    world_event_count: int


class ActRunner:
    def __init__(
        self,
        scenario: Scenario,
        participants: dict[str, Participant],
        registry: ActionRegistry,
        router: TurnRouter,
        *,
        seed: int,
        max_retries: int = 2,
        recorder: object | None = None,
    ) -> None:
        missing = set(scenario.world.character_ids) - set(participants)
        if missing:
            raise ValueError(f"missing Participant adapters for Characters: {sorted(missing)}")
        self.scenario = scenario
        self.participants = participants
        self.registry = registry
        self.router = router
        self.max_retries = max_retries
        self.recorder = recorder or NullRunRecorder()
        self.harness = WorldHarness(scenario.world, registry)
        self.runtimes = {
            actor_id: AgentRuntime(actor_id=actor_id)
            for actor_id in scenario.world.character_ids
        }
        self.observation_listeners = [self.recorder.record_observation]
        self.event_listeners = []
        self.observation = ObservationSystem(
            self.runtimes,
            registry,
            seed=seed + 1,
            listeners=self.observation_listeners,
            trace_listener=self.recorder.record_trace,
        )
        self.context_projector = ContextProjector()
        self.rounds_completed = 0
        self.turns_completed = 0

    @property
    def state(self):
        return self.harness.state

    def add_observation_listener(self, listener) -> None:
        self.observation_listeners.append(listener)

    def add_world_event_listener(self, listener) -> None:
        self.event_listeners.append(listener)

    def run(self, max_rounds: int | None = None) -> RunResult:
        rounds = self.scenario.max_rounds if max_rounds is None else max_rounds
        if rounds < 1:
            raise ValueError("max_rounds must be at least one")
        try:
            for round_number in range(1, rounds + 1):
                self.run_round(round_number)
            result = RunResult(
                rounds_completed=self.rounds_completed,
                turns_completed=self.turns_completed,
                world_event_count=len(self.harness.world_log),
            )
            self.recorder.finalize(
                state=self.state,
                participants=self.participants,
                result=result,
                status="completed",
            )
            return result
        except Exception as exc:
            self.recorder.finalize(
                state=self.state,
                participants=self.participants,
                result=None,
                status="failed",
                error=str(exc),
            )
            raise

    def run_round(self, round_number: int) -> list[str]:
        order = self.router.order(self.state.character_ids, round_number)
        self.recorder.record_router(round_number, order)
        for actor_id in order:
            self._run_turn(actor_id, round_number)
            self.turns_completed += 1
        self.rounds_completed += 1
        return order

    def _run_turn(self, actor_id: str, round_number: int) -> None:
        runtime = self.runtimes[actor_id]
        environment = self.observation.scan_environment(self.state, actor_id, round_number)
        context = self.context_projector.build(self.state, runtime, environment, round_number)
        request = DecisionRequest(actor_id=actor_id, context=context)
        accepted: AcceptedTurn | None = None
        for attempt in range(1, self.max_retries + 2):
            outcome = "rejected"
            attempt_notices: list[ExecutionNotice] = []
            try:
                decision = self.participants[actor_id].decide(request)
            except AgentUnavailableError:
                raise
            except AgentError as exc:
                decision = AgentDecision(
                    actor_id=actor_id,
                    raw_content="",
                    output_error=str(exc),
                )
            issues: list[ValidationIssue] = []
            if decision.output_error:
                issues.append(ValidationIssue(code="output_error", message=decision.output_error))
            elif decision.plan is None:
                issues.append(ValidationIssue(code="missing_plan", message="参与者没有提供可解析的 TurnPlan"))
            elif decision.actor_id != actor_id:
                issues.append(ValidationIssue(code="wrong_actor", message="参与者不能替其他角色行动"))
            else:
                runtime.private_thoughts.append(decision.plan.private_thought)
                resolution = self.harness.resolve(
                    actor_id,
                    decision.plan,
                    round_number,
                    known_entity_ids=runtime.knowledge.entities,
                )
                if isinstance(resolution, RejectedTurn):
                    truncated = self._try_truncate_after_move(
                        actor_id,
                        decision.plan,
                        resolution,
                        round_number,
                    )
                    if truncated is None:
                        issues.extend(resolution.validation_issues)
                    else:
                        accepted, cutoff_frame = truncated
                        attempt_notices.extend(accepted.execution_notices)
                        failed_reasons = "；".join(
                            issue.message for issue in resolution.validation_issues
                        )
                        attempt_notices.append(
                            ExecutionNotice(
                                code="move_truncated",
                                message=(
                                    "计划在移动后被截断：移动已经发生，该 move frame "
                                    f"之后的 action 均未执行。原始原因：{failed_reasons}"
                                ),
                                frame_index=cutoff_frame,
                                unexecuted_from_frame_index=cutoff_frame + 1,
                                unexecuted_through_frame_index=len(decision.plan.frames) - 1,
                            )
                        )
                        outcome = "truncated"
                        self.recorder.record_trace(
                            "turn_truncated",
                            {
                                "round_number": round_number,
                                "actor_id": actor_id,
                                "cutoff_frame_index": cutoff_frame,
                                "issues": [
                                    issue.model_dump(mode="json")
                                    for issue in resolution.validation_issues
                                ],
                            },
                        )
                else:
                    accepted = resolution
                    attempt_notices.extend(resolution.execution_notices)
                    outcome = (
                        "accepted_with_notice"
                        if attempt_notices
                        else "accepted"
                    )
            if accepted is None and attempt == self.max_retries + 1:
                outcome = "fallback"
            self.recorder.record_decision(
                round_number=round_number,
                actor_id=actor_id,
                attempt=attempt,
                decision=decision,
                accepted=accepted is not None,
                outcome=outcome,
                reasons=[issue.message for issue in issues],
                notices=attempt_notices,
            )
            if accepted is not None:
                runtime.execution_notices.extend(attempt_notices)
                for notice in attempt_notices:
                    self.recorder.record_trace(
                        "execution_notice",
                        {
                            "round_number": round_number,
                            "actor_id": actor_id,
                            "notice": notice.model_dump(mode="json"),
                        },
                    )
                break
            runtime.last_validation_error = "；".join(issue.message for issue in issues)
            request = DecisionRequest(
                actor_id=actor_id,
                feedback=ValidationFeedback(issues=issues),
            )

        if accepted is None:
            fallback = self.registry.parse_plan(
                {
                    "private_thought": "提交的计划无法执行，暂时等待。",
                    "frames": [{"commands": [{"kind": "wait"}]}],
                }
            )
            resolution = self.harness.resolve(
                actor_id,
                fallback,
                round_number,
                known_entity_ids=runtime.knowledge.entities,
            )
            if not isinstance(resolution, AcceptedTurn):
                raise RuntimeError("fallback wait was rejected")
            accepted = resolution
            self.recorder.record_fallback(
                round_number=round_number,
                actor_id=actor_id,
            )

        runtime.last_validation_error = None
        for frame in accepted.committed_frames:
            self.observation.project_frame(frame)
            self.observation.apply_directives(
                frame.after_state, frame.directives, round_number
            )
            for event in frame.events:
                transcript = (
                    render_world_event(frame.after_state, event, full=True)
                    if event.source == EventSource.WORLD
                    else self.registry.render(frame.after_state, event, full=True)
                )
                self.recorder.record_world_event(event, transcript)
                for listener in self.event_listeners:
                    listener(event, transcript)

    def _try_truncate_after_move(
        self,
        actor_id: str,
        plan: TurnPlan,
        rejection: RejectedTurn,
        round_number: int,
    ) -> tuple[AcceptedTurn, int] | None:
        issues = rejection.validation_issues
        if not issues or any(issue.code != "action_invalid" for issue in issues):
            return None
        failed_frames = {issue.frame_index for issue in issues}
        if None in failed_frames or len(failed_frames) != 1:
            return None
        failed_frame = next(iter(failed_frames))
        assert failed_frame is not None

        for issue in issues:
            if issue.command_index is None:
                return None
            command = plan.frames[failed_frame].commands[issue.command_index]
            if not self.registry.spec(command.kind).stale_after_move_recoverable:
                return None

        current_room = self.state.root_room_of(actor_id)
        simulated_room = current_room
        cutoff_frame: int | None = None
        for frame_index, frame in enumerate(plan.frames[:failed_frame]):
            for command in frame.commands:
                spec = self.registry.spec(command.kind)
                if not spec.is_move_checkpoint:
                    continue
                destination = getattr(command, "destination_room_id", None)
                if destination is not None and destination != simulated_room:
                    simulated_room = destination
                    cutoff_frame = frame_index
        if cutoff_frame is None:
            return None

        prefix = TurnPlan(
            private_thought=plan.private_thought,
            frames=[
                frame.model_copy(deep=True)
                for frame in plan.frames[: cutoff_frame + 1]
            ],
        )
        resolution = self.harness.resolve(
            actor_id,
            prefix,
            round_number,
            known_entity_ids=self.runtimes[actor_id].knowledge.entities,
        )
        if not isinstance(resolution, AcceptedTurn):
            return None
        if not any(
            event.source == EventSource.ACTION
            and event.action_kind is not None
            and self.registry.spec(event.action_kind).is_move_checkpoint
            for event in resolution.events
        ):
            return None
        return resolution, cutoff_frame
