"""Deterministically replay schema-v2 decisions without an LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from token_odyssey.agents.contracts import AgentDecision
from token_odyssey.agents.scripted import ReplayAgent
from token_odyssey.inside_act.actions import build_builtin_registry
from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.inside_act.router import ShuffledRoundRouter
from token_odyssey.inside_act.runner import ActRunner


@dataclass(frozen=True)
class ReplayReport:
    success: bool
    event_count: int
    events_match: bool
    final_state_matches: bool


def replay_run(run_dir: str | Path) -> ReplayReport:
    path = Path(run_dir)
    manifest = _read_json(path / "manifest.json")
    if manifest.get("schema_version") != 2:
        raise ValueError("only run schema v2 can be replayed")
    scenario = Scenario.model_validate(_read_json(path / "scenario.json"))
    registry = build_builtin_registry()
    decisions: dict[str, list[AgentDecision]] = {
        actor_id: [] for actor_id in scenario.world.character_ids
    }
    for row in _read_jsonl(path / "decisions.jsonl"):
        raw = dict(row["decision"])
        plan_raw = raw.pop("plan", None)
        if row.get("accepted", False):
            plan = (
                registry.parse_plan(_migrate_v2_plan(plan_raw))
                if plan_raw is not None
                else None
            )
        else:
            # Replays reproduce the recorded world, not today's possibly looser
            # validation policy. A historically rejected intent must stay rejected.
            plan = None
            if not raw.get("output_error"):
                reasons = "；".join(row.get("reasons") or ["历史运行拒绝了该计划"])
                raw["output_error"] = f"replay 保留历史拒绝：{reasons}"
        decisions[row["actor_id"]].append(AgentDecision.model_validate({**raw, "plan": plan}))
    replay_agent = ReplayAgent(decisions)
    runner = ActRunner(
        scenario,
        {actor_id: replay_agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(int(manifest["seed"])),
        seed=int(manifest["seed"]),
    )
    result_data = manifest.get("result") or {}
    rounds = int(result_data.get("rounds_completed") or scenario.max_rounds)
    result = runner.run(rounds)
    expected_events = _read_jsonl(path / "world_events.jsonl")
    actual_events = [event.model_dump(mode="json") for event in runner.harness.world_log]
    expected_state = _read_json(path / "final_state.json")
    actual_state = runner.state.model_dump(mode="json")
    events_match = actual_events == expected_events
    state_matches = actual_state == expected_state
    return ReplayReport(
        success=events_match and state_matches,
        event_count=result.world_event_count,
        events_match=events_match,
        final_state_matches=state_matches,
    )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _migrate_v2_plan(plan_raw: dict) -> dict:
    migrated = json.loads(json.dumps(plan_raw))
    for frame in migrated.get("frames", []):
        for command in frame.get("commands", []):
            if command.get("kind") == "place" and "relation" not in command:
                command["relation"] = "inside"
    return migrated
