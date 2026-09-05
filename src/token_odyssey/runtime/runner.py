"""Serial batches with a single-action commit boundary and a resumable input port."""

from dataclasses import dataclass

from token_odyssey.agents.contracts import DecisionRequest, InputRequired, Participant
from token_odyssey.common import FrozenModel
from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.kernel.events import Issue, WorldEvent
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.kernel.harness import WorldHarness
from token_odyssey.perception.system import ObservationSystem
from token_odyssey.recording import NullRecorder, Recorder
from token_odyssey.runtime.router import ShuffledRouter, TurnRouter
from token_odyssey.scenario import RoleBrief, Scenario


class RunResult(FrozenModel):
    status: str
    turns_completed: int
    rounds_completed: int
    transactions: int
    events: int
    goals_met: bool


@dataclass
class PendingTurn:
    request: DecisionRequest
    known_ids: frozenset[str]
    attempt: int = 0


class ActRunner:
    def __init__(self, scenario: Scenario, participants: dict[str, Participant], registry: ActionRegistry,
                 *, router: TurnRouter | None = None, seed: int | None = None, recorder: Recorder | None = None):
        if set(participants) != set(scenario.world.character_ids):
            raise ValueError("participants must match all Characters exactly")
        self.scenario, self.participants, self.registry = scenario, participants, registry
        actual_seed = scenario.seed if seed is None else seed
        self.router = router or ShuffledRouter(actual_seed)
        self.recorder = recorder or NullRecorder()
        self.harness = WorldHarness(scenario.create_world(), registry)
        self.observation = ObservationSystem(scenario.world.character_ids, actual_seed + 1,
                                             on_observation=lambda o: self.recorder.record("observations", o),
                                             on_sample=lambda sample: self.recorder.record("perception_samples", sample))
        for actor_id in scenario.world.character_ids:
            brief = scenario.roles.get(actor_id, RoleBrief())
            self.observation.initialize_known(self.harness.world, actor_id, brief.known_entity_ids)
        self.turns_completed = 0
        self.events: list[WorldEvent] = []
        self._router_cursor = 0
        self.pending: PendingTurn | None = None

    @property
    def goals_met(self) -> bool:
        return bool(self.scenario.end_when) and all(
            Fluents(self.harness.world).satisfies(atom) for atom in self.scenario.end_when
        )

    def step(self) -> str:
        """Complete one selected turn, or return waiting_for_input without advancing.

        Retrying a malformed/wholly rejected proposal is allowed. Once any action
        is accepted, a later failure ends the turn; its prefix is never replayed.
        """
        if self.goals_met:
            return "completed"
        policy = self.scenario.turn_policy
        if self.pending is None:
            actor_id = self.router.next_actor(self.scenario.world.character_ids, tuple(self.events[self._router_cursor:]))
            if actor_id not in self.participants:
                raise ValueError("router selected an unknown Character")
            self._router_cursor = len(self.events)
            self.recorder.record("routing", {"turn": self.turns_completed + 1, "actor_id": actor_id})
            view = self.observation.view(self.harness.world, actor_id, max_actions=policy.max_actions,
                                         continue_after_move=policy.continue_after_move)
            self.recorder.record("views", view)
            request = DecisionRequest(request_id=f"turn-{self.turns_completed + 1}-attempt-1", actor_id=actor_id, view=view)
            self.pending = PendingTurn(request, self.observation.known_ids(actor_id))
            self.recorder.record("requests", request)

        while self.pending is not None:
            pending = self.pending
            request, actor_id = pending.request, pending.request.actor_id
            try:
                decision = self.participants[actor_id].decide(request.model_copy(deep=True))
            except InputRequired:
                return "waiting_for_input"
            self.recorder.record("decisions", {"request_id": request.request_id, "decision": decision})
            issues: tuple[Issue, ...] = ()
            batch = None
            if decision.actor_id != actor_id:
                issues = (Issue(code="WRONG_ACTOR"),)
            elif decision.error is not None:
                issues = (Issue(code="INVALID_OUTPUT", details={"reason": decision.error}),)
            else:
                try:
                    batch = self.registry.parse_batch(decision.batch.model_dump(mode="json"))
                except ValueError as exc:
                    issues = (Issue(code="INVALID_OUTPUT", details={"reason": str(exc)}),)
                if batch is not None and len(batch.actions) > policy.max_actions:
                    issues = (Issue(code="BATCH_TOO_LONG", details={"maximum": policy.max_actions}),)

            accepted_count = 0
            if not issues and batch is not None:
                for index, intent in enumerate(batch.actions):
                    result = self.harness.execute(actor_id, intent, known_ids=pending.known_ids)
                    self.recorder.record("action_results", {"request_id": request.request_id, "action_index": index,
                                                           "kind": intent.kind, "accepted": result.accepted,
                                                           "transaction_id": result.transaction.id if result.transaction else None,
                                                           "issues": result.issues, "notices": result.notices})
                    if not result.accepted:
                        issues = result.issues
                        if accepted_count:
                            self.observation.memories[actor_id].feedback.extend((*issues, Issue(code="BATCH_STOPPED", details={
                                "successful_actions": accepted_count, "failed_action": index + 1,
                                "unexecuted_actions": len(batch.actions) - index,
                            })))
                        break
                    accepted_count += 1
                    self._publish(result, actor_id)
                    if result.ends_batch and not policy.continue_after_move:
                        if index + 1 < len(batch.actions):
                            self.observation.memories[actor_id].feedback.append(Issue(code="MOVE_ENDS_BATCH", details={
                                "unexecuted_actions": len(batch.actions) - index - 1,
                            }))
                        break
                    if self.goals_met:
                        break
            if accepted_count:
                self.pending = None
                self.turns_completed += 1
                return "completed" if self.goals_met else "turn_completed"

            pending.attempt += 1
            if pending.attempt > policy.max_retries:
                fallback = self.registry.parse_intent({"kind": "wait"})
                result = self.harness.execute(actor_id, fallback, known_ids=pending.known_ids)
                self._publish(result, actor_id)
                self.observation.memories[actor_id].feedback.extend((*issues, Issue(code="FALLBACK_WAIT")))
                self.recorder.record("fallbacks", {"actor_id": actor_id, "request_id": request.request_id})
                self.pending = None
                self.turns_completed += 1
                return "turn_completed"
            pending.request = DecisionRequest(request_id=f"turn-{self.turns_completed + 1}-attempt-{pending.attempt + 1}",
                                               actor_id=actor_id, view=request.view, issues=issues)
            self.recorder.record("requests", pending.request)
        raise RuntimeError("unreachable turn state")

    def _publish(self, result, actor_id: str) -> None:
        if result.transaction:
            self.recorder.record("transactions", result.transaction)
            for event in result.transaction.events:
                self.events.append(event)
                self.recorder.record("events", event)
            self.observation.project(result)
        self.observation.memories[actor_id].feedback.extend(result.notices)
        if result.rescan_actor:
            self.observation.scan(self.harness.world, actor_id)

    def run(self, max_rounds: int | None = None) -> RunResult:
        rounds = self.scenario.max_rounds if max_rounds is None else max_rounds
        if rounds < 1:
            raise ValueError("max_rounds must be positive")
        limit = rounds * len(self.participants)
        status = "limit_reached"
        try:
            while self.turns_completed < limit and not self.goals_met:
                status = self.step()
                if status == "waiting_for_input":
                    break
            if self.goals_met:
                status = "completed"
            elif status != "waiting_for_input":
                status = "limit_reached"
            result = RunResult(status=status, turns_completed=self.turns_completed,
                               rounds_completed=self.turns_completed // len(self.participants),
                               transactions=len(self.harness.world_log), events=len(self.events), goals_met=self.goals_met)
            self.recorder.finalize(state=self.harness.world.state, result=result, status=status)
            return result
        except Exception as exc:
            self.recorder.finalize(state=self.harness.world.state, result=None, status="failed", error=type(exc).__name__)
            raise
