"""Replay a recorded run without invoking an LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from airpg.agents import RecordedAgent
from airpg.engine import GameEngine
from airpg.models import AgentDecision, Scenario


@dataclass(frozen=True)
class ReplayReport:
    success: bool
    event_count: int
    events_match: bool
    final_state_matches: bool


def replay_run(run_dir: str | Path) -> ReplayReport:
    path = Path(run_dir)
    manifest = _read_json(path / "manifest.json")
    scenario = Scenario.model_validate(_read_json(path / "scenario.json"))
    decisions: dict[str, list[AgentDecision]] = {
        actor_id: [] for actor_id in scenario.world.actors
    }
    for row in _read_jsonl(path / "decisions.jsonl"):
        decision = AgentDecision.model_validate(row["decision"])
        decisions[row["actor_id"]].append(decision)

    recorded = RecordedAgent(decisions)
    engine = GameEngine(
        scenario,
        {actor_id: recorded for actor_id in scenario.world.actors},
        seed=int(manifest["seed"]),
    )
    result_data = manifest.get("result") or {}
    rounds = int(result_data.get("rounds_completed") or scenario.max_rounds)
    result = engine.run(rounds)

    expected_events = _read_jsonl(path / "world_events.jsonl")
    actual_events = [event.model_dump(mode="json") for event in engine.harness.world_log]
    expected_state = _read_json(path / "final_state.json")
    actual_state = engine.state.model_dump(mode="json")
    events_match = actual_events == expected_events
    state_match = actual_state == expected_state
    return ReplayReport(
        success=events_match and state_match,
        event_count=result.world_event_count,
        events_match=events_match,
        final_state_matches=state_match,
    )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

