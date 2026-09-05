"""Replay committed changes and recorded subjective views, without executing agents.

This is log playback, not rerunning intents with today's rules or resampling
perception. Recorded observations and views preserve exactly what was supplied.
"""

import json
from pathlib import Path

from token_odyssey.common import FrozenModel
from token_odyssey.kernel.events import Transaction
from token_odyssey.kernel.state import WorldState, apply_changes
from token_odyssey.perception.models import ActorView, Observation
from token_odyssey.scenario import Scenario


class ReplayReport(FrozenModel):
    success: bool
    transactions: int
    events: int
    final_state_matches: bool
    events_match: bool
    projection_records_valid: bool
    views: tuple[ActorView, ...]


def replay_run(run_dir: str | Path) -> ReplayReport:
    path = Path(run_dir)
    manifest = _read(path / "manifest.json")
    if manifest.get("schema_version") != 4:
        raise ValueError("only run schema 4 is supported")
    scenario = Scenario.model_validate(_read(path / "scenario.json"))
    world = scenario.create_world()
    if world.state != WorldState.model_validate(_read(path / "initial_state.json")):
        raise ValueError("initial snapshot differs from scenario")
    transactions = [Transaction.model_validate(row) for row in _rows(path / "transactions.jsonl")]
    events = []
    for index, transaction in enumerate(transactions, 1):
        if transaction.id != index or transaction.before_revision != world.state.revision:
            raise ValueError("transaction sequence/revision mismatch")
        if transaction.after_revision != transaction.before_revision + 1:
            raise ValueError("invalid successor revision")
        for event in transaction.events:
            if event.sequence != len(events) + 1 or event.transaction_id != transaction.id:
                raise ValueError("event sequence mismatch")
            if event.caused_by is not None and event.caused_by not in {e.sequence for e in transaction.events if e.sequence < event.sequence}:
                raise ValueError("invalid event cause")
            apply_changes(world.state, event.changes)
            world.validate()
            events.append(event)
        world.state.revision = transaction.after_revision
    expected = WorldState.model_validate(_read(path / "final_state.json"))
    state_match = world.state == expected
    events_match = [event.model_dump(mode="json") for event in events] == _rows(path / "events.jsonl")
    observations = [Observation.model_validate(row) for row in _rows(path / "observations.jsonl")]
    by_id = {o.sequence: o for o in observations}
    event_ids = {e.sequence for e in events}
    projections_valid = all(
        o.sequence == i and o.observer_id in scenario.world.character_ids
        and 0 <= o.world_revision <= world.state.revision
        and (o.source_event_sequence is None or o.source_event_sequence in event_ids)
        for i, o in enumerate(observations, 1)
    )
    views = tuple(ActorView.model_validate(row) for row in _rows(path / "views.jsonl"))
    projections_valid = projections_valid and all(
        view.actor_id in scenario.world.character_ids and all(
            o.observer_id == view.actor_id and by_id.get(o.sequence) == o for o in view.observations
        ) for view in views
    )
    return ReplayReport(success=state_match and events_match and projections_valid,
                        transactions=len(transactions), events=len(events), final_state_matches=state_match,
                        events_match=events_match, projection_records_valid=projections_valid, views=views)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []
