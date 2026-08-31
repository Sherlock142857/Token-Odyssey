"""Round router and the complete within-Act game loop."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from airpg.agents import Agent, AgentError, AgentUnavailableError
from airpg.context import ContextBuilder
from airpg.debug import DebugSink, NullDebugSink
from airpg.harness import IntentRejected, WorldHarness
from airpg.models import (
    ActionKind,
    AgentRuntime,
    AgentSession,
    Observation,
    ObservationLevel,
    Scenario,
    TurnIntent,
    WaitActionIntent,
    WorldEvent,
)
from airpg.harness.observation import ObservationSystem
from airpg.recording import NullRunRecorder


@dataclass(frozen=True)
class RunResult:
    rounds_completed: int
    turns_completed: int
    world_event_count: int


class GameEngine:
    def __init__(
        self,
        scenario: Scenario,
        agents: dict[str, Agent],
        *,
        seed: int | None = None,
        max_retries: int = 2,
        debug: DebugSink | None = None,
        recorder: object | None = None,
    ) -> None:
        missing = set(scenario.world.actors) - set(agents)
        if missing:
            raise ValueError(f"missing agent adapters for actors: {sorted(missing)}")
        self.scenario = scenario
        self.state = scenario.world
        self.agents = agents
        self.max_retries = max_retries
        self.debug = debug or NullDebugSink()
        self.recorder = recorder or NullRunRecorder()
        actual_seed = scenario.seed if seed is None else seed
        self.router_rng = random.Random(actual_seed)
        self.runtimes = {
            actor_id: AgentRuntime(actor_id=actor_id) for actor_id in self.state.actors
        }
        self.sessions = {
            actor_id: AgentSession(actor_id=actor_id) for actor_id in self.state.actors
        }
        self.observation_listeners: list[Callable[[Observation], None]] = [
            self.recorder.record_observation
        ]
        self.world_event_listeners: list[Callable[[WorldEvent, str], None]] = []
        self.harness = WorldHarness(self.state, self.debug)
        self.observation = ObservationSystem(
            self.state,
            self.runtimes,
            seed=actual_seed + 1,
            debug=self.debug,
            listeners=self.observation_listeners,
            recorder=self.recorder,
        )
        self.context_builder = ContextBuilder(scenario, self.debug, self.recorder)
        self.turns_completed = 0
        self.rounds_completed = 0
        self._initialized = False

    def add_observation_listener(self, listener: Callable[[Observation], None]) -> None:
        """Register a future player/UI stream without giving it canonical world access."""
        self.observation_listeners.append(listener)

    def add_world_event_listener(
        self, listener: Callable[[WorldEvent, str], None]
    ) -> None:
        """Register a presentation listener for accepted canonical events."""
        self.world_event_listeners.append(listener)

    def initialize(self) -> None:
        if self._initialized:
            return
        for actor_id in self.state.actors:
            self.observation.scan_environment(
                actor_id,
                0,
                force_context_update=True,
                reason="Act 开始，你观察了周围环境。",
            )
        self._initialized = True

    def run(self, max_rounds: int | None = None) -> RunResult:
        rounds = self.scenario.max_rounds if max_rounds is None else max_rounds
        if rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        try:
            self.initialize()
            for round_number in range(1, rounds + 1):
                self.run_round(round_number)
            result = RunResult(
                rounds_completed=self.rounds_completed,
                turns_completed=self.turns_completed,
                world_event_count=len(self.harness.world_log),
            )
            self.recorder.finalize(
                state=self.state,
                sessions=self.sessions,
                result=result,
                status="completed",
            )
            return result
        except Exception as exc:
            self.recorder.finalize(
                state=self.state,
                sessions=self.sessions,
                result=None,
                status="failed",
                error=str(exc),
            )
            raise

    def run_round(self, round_number: int) -> list[str]:
        self.initialize()
        order = list(self.state.actors)
        self.router_rng.shuffle(order)
        self.debug.emit("router", f"第 {round_number} 轮行动顺序", order)
        self.recorder.record_router(round_number, order)
        for actor_id in order:
            self._run_turn(actor_id, round_number)
            self.turns_completed += 1
        self.rounds_completed += 1
        return order

    def _run_turn(self, actor_id: str, round_number: int) -> None:
        runtime = self.runtimes[actor_id]
        session = self.sessions[actor_id]
        self.observation.scan_environment(actor_id, round_number)
        context = self.context_builder.begin_turn(session, runtime, round_number)

        accepted: TurnIntent | None = None
        for attempt in range(self.max_retries + 1):
            try:
                decision = self.agents[actor_id].decide(context)
            except AgentUnavailableError as exc:
                self.debug.emit(
                    "agent_unavailable",
                    f"{actor_id} 的模型服务不可用，终止 Act",
                    {"reason": str(exc)},
                )
                raise RuntimeError(str(exc)) from exc
            except AgentError as exc:
                reason = str(exc)
                self.debug.emit(
                    "agent_error",
                    f"{actor_id} 第 {attempt + 1} 次生成失败",
                    {"reason": reason},
                )
                context = self.context_builder.append_feedback(
                    session,
                    runtime,
                    [reason],
                    round_number=round_number,
                )
                continue

            context = self.context_builder.append_assistant(
                session,
                runtime,
                decision.raw_content,
                round_number=round_number,
            )
            reasons: list[str] = []
            intent = decision.intent
            if decision.output_error:
                reasons.append(decision.output_error)
            elif intent is None:
                reasons.append("模型没有提供可解析的 TurnIntent")
            elif intent.actor_id != actor_id:
                reasons.append(f"角色 {actor_id} 不能替 {intent.actor_id} 行动")
            else:
                reasons = self._knowledge_reasons(actor_id, intent)
                reasons.extend(self.harness.validate(intent))
            if intent is not None and intent.private_thought:
                runtime.private_thoughts.append(intent.private_thought)
                self.debug.emit(
                    "private_thought",
                    f"{actor_id} 保存第 {attempt + 1} 次生成的私有想法",
                    {"private_thought": intent.private_thought},
                )
            self.debug.emit("intent", f"{actor_id} 提交意图", intent or decision.raw_content)
            self.recorder.record_decision(
                round_number=round_number,
                actor_id=actor_id,
                attempt=attempt + 1,
                decision=decision,
                accepted=not reasons,
                reasons=reasons,
            )
            if reasons:
                self.debug.emit(
                    "validation_rejected",
                    f"{actor_id} 第 {attempt + 1} 次意图被拒绝",
                    {"reasons": reasons},
                )
                context = self.context_builder.append_feedback(
                    session,
                    runtime,
                    reasons,
                    round_number=round_number,
                )
                continue
            accepted = intent
            break

        if accepted is None:
            accepted = TurnIntent(
                actor_id=actor_id,
                private_thought="我的意图无法执行，只能暂时等待。",
                action=WaitActionIntent(),
            )
            self.debug.emit(
                "fallback",
                f"{actor_id} 连续失败，自动执行 wait",
                accepted,
            )

        runtime.last_validation_error = None
        try:
            events = self.harness.execute(accepted, round_number)
        except IntentRejected as exc:  # Defensive: state cannot change between validate/execute here.
            raise RuntimeError(f"validated intent became invalid: {exc}") from exc

        for event in events:
            self.observation.project_event(event)
            transcript = self.observation.render_event(event, ObservationLevel.FULL)
            self.recorder.record_world_event(event, transcript)
            self.recorder.record_state_change(event)
            for listener in self.world_event_listeners:
                listener(event, transcript)

        action = accepted.action
        if action is not None and action.kind == ActionKind.SEARCH and action.target_item_id:
            self.observation.reveal_container(actor_id, action.target_item_id, round_number)
        if action is not None and action.kind == ActionKind.MOVE:
            self.observation.scan_environment(
                actor_id,
                round_number,
                force_context_update=True,
                reason="移动完成后，你重新确认了环境。",
            )

    def _knowledge_reasons(self, actor_id: str, intent: TurnIntent) -> list[str]:
        """Prevent an agent from acting on canonical IDs it has never observed."""
        if intent.action is None:
            return []
        known_ids = self.runtimes[actor_id].knowledge.items
        referenced = {
            item_id
            for item_id in (
                getattr(intent.action, "target_item_id", None),
                getattr(intent.action, "container_id", None),
            )
            if item_id is not None
        }
        unknown = sorted(referenced - set(known_ids))
        return [f"你尚未观察到物品 {item_id!r}，不能直接对它行动" for item_id in unknown]
