"""The single writer: one action plus immediate reactions forms a transaction."""

from token_odyssey.kernel.actions.base import ActionContext, Intent
from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.kernel.events import ActionResult, EventDraft, EventFrame, Issue, Transaction, WorldEvent
from token_odyssey.kernel.mechanics import MechanicsEngine
from token_odyssey.kernel.state import World, apply_changes


class WorldHarness:
    def __init__(self, world: World, registry: ActionRegistry):
        world.validate()
        self._world = world.snapshot()
        self.registry = registry
        self.mechanics = MechanicsEngine()
        self._log: list[Transaction] = []
        self._event_count = 0

    @property
    def world(self) -> World:
        return self._world.snapshot()

    @property
    def world_log(self) -> tuple[Transaction, ...]:
        return tuple(t.model_copy(deep=True) for t in self._log)

    def execute(self, actor_id: str, intent: Intent, *, known_ids: frozenset[str]) -> ActionResult:
        """Knowledge is fixed at decision time, not expanded by guessed future IDs.

        The outer runner controls batches. It must never resubmit a committed
        prefix while repairing a later failed action.
        """
        if actor_id not in self._world.definition.character_ids:
            return ActionResult(False, issues=(Issue(code="UNKNOWN_ACTOR"),))
        try:
            action = self.registry.get(intent.kind)
            # Validate typed objects from non-LLM participants as well.
            intent = action.intent_type.model_validate(intent.model_dump(mode="python"))
        except ValueError:
            return ActionResult(False, issues=(Issue(code="INVALID_INTENT"),))
        unknown = action.references(intent) - known_ids
        if unknown:
            return ActionResult(False, issues=(Issue(code="UNKNOWN_TO_ACTOR", details={"ids": sorted(unknown)}),))
        context = ActionContext(actor_id, self.world)
        issues = action.poss(context, intent)
        if issues:
            return ActionResult(False, issues=issues)
        plan = action.effects(context, intent)
        if plan.event is None:
            return ActionResult(True, notices=plan.notices)

        draft = self.world
        frames: list[EventFrame] = []
        transaction_id = len(self._log) + 1

        def stage(event_draft: EventDraft, caused_by: int | None = None) -> WorldEvent:
            event = WorldEvent(**event_draft.model_dump(mode="python"),
                               sequence=self._event_count + len(frames) + 1,
                               transaction_id=transaction_id, caused_by=caused_by)
            objects = set(draft.definition.entities) | set(draft.definition.passages)
            actors = set(draft.definition.character_ids)
            for cue in event.cues:
                references = {cue.anchor_id, *cue.identifies, *cue.locates,
                              *(anchor.object_id for anchor in cue.requires)}
                observers = set(cue.certain_for) | set(cue.only_for or ())
                if references - objects or observers - actors:
                    raise ValueError("observation cue references an unknown object or Character")
            before = draft.snapshot()
            apply_changes(draft.state, event.changes)
            draft.state.revision = self._world.state.revision + 1
            draft.validate()
            frames.append(EventFrame(event, before, draft.snapshot()))
            return event

        try:
            stage(plan.event)
            cursor = 0
            reactions = 0
            while cursor < len(frames):
                event = frames[cursor].event
                cursor += 1
                # Generator conditions see the latest draft after earlier rules.
                for rule in self.mechanics.matching(draft, event):
                    reactions += 1
                    if reactions > draft.definition.max_reactions_per_action:
                        raise ValueError("mechanic reaction limit exceeded; possible causal cycle")
                    stage(self.mechanics.reaction(draft, rule), event.sequence)
        except ValueError as exc:
            # This is a scenario/programming fault, not a repairable player error.
            # No draft state, events, perception, or RNG consumption is committed.
            raise WorldExecutionError(str(exc)) from exc

        before_revision = self._world.state.revision
        draft.state.revision = before_revision + 1
        transaction = Transaction(id=transaction_id, actor_id=actor_id, action_kind=intent.kind,
                                  before_revision=before_revision, after_revision=draft.state.revision,
                                  events=tuple(frame.event for frame in frames))
        # The only canonical commit point. Disk recording is a downstream concern;
        # this demo does not claim database durability across process crashes.
        self._world = draft
        self._log.append(transaction.model_copy(deep=True))
        self._event_count += len(frames)
        return ActionResult(True, transaction=transaction, frames=tuple(frames), notices=plan.notices,
                            rescan_actor=plan.rescan_actor, ends_batch=plan.ends_batch)


class WorldExecutionError(RuntimeError):
    pass
