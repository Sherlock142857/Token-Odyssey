"""Reusable full-flow acceptance entry point, independent of pytest and networks."""

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.common import FrozenModel
from token_odyssey.config.models import ParticipantConfig, RunConfig
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.llm.contracts import LLMProfile, LLMRequest, LLMResponse
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.composition import build_participants, build_scripted_participants, identity_for
from token_odyssey.runtime.runner import ActRunner, RunResult
from token_odyssey.scenario import load_scenario
from token_odyssey.translators.llm import LLMTranslator


class ScriptedResponseBackend:
    """Fake only the transport response; exercise the real translator and session."""

    def __init__(self, batches: Iterable[dict[str, Any]]) -> None:
        self.responses = deque(json.dumps(raw, ensure_ascii=False) for raw in batches)

    def complete(self, request: LLMRequest) -> LLMResponse:
        content = self.responses.popleft() if self.responses else '{"actions":[{"kind":"wait"}]}'
        return LLMResponse(content=content, model=request.profile.model)


class AcceptanceReport(FrozenModel):
    success: bool
    mode: str
    run_dir: str
    result: RunResult
    expected: tuple[bool, ...]
    replay_matches: bool


def run_acceptance(scenario_path: str | Path, *, root: str | Path = "runs", mode: str = "scripted") -> AcceptanceReport:
    """Run the deterministic, offline acceptance path and verify its recorded replay."""

    registry = builtin_registry()
    scenario = load_scenario(scenario_path, registry)
    if not scenario.expected:
        raise ValueError("an acceptance scenario must declare expected predicates")
    recorder = RunRecorder(scenario, root=root)
    if mode == "scripted":
        participants = build_scripted_participants(scenario, registry)
    elif mode == "translated":
        participants = {
            actor: LLMAgent(
                actor,
                LLMTranslator(registry, identity_for(scenario, actor)),
                ScriptedResponseBackend(scenario.scripts.get(actor, ())),
                LLMProfile(backend_id="offline", model="scripted-response"),
                on_exchange=lambda exchange: recorder.record("llm_exchanges", exchange),
            )
            for actor in scenario.world.character_ids
        }
    else:
        raise ValueError("acceptance mode must be scripted or translated")
    runner = ActRunner(scenario, participants, registry, recorder=recorder)
    result = runner.run()
    report = _acceptance_report(scenario, runner, recorder, result, mode=mode)
    (recorder.run_dir / "acceptance.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def run_live_acceptance(
    scenario_path: str | Path,
    config: RunConfig,
    *,
    root: str | Path = "runs",
    rounds: int | None = None,
    profile: str | None = None,
) -> AcceptanceReport:
    """Run an all-LLM act against a real configured backend and verify its replay.

    Unlike :func:`run_acceptance`, this function performs billable network calls.
    Callers are responsible for making that distinction explicit to users.
    """

    registry = builtin_registry()
    scenario = load_scenario(scenario_path, registry)
    if not scenario.expected or not scenario.end_when:
        raise ValueError("a live acceptance scenario must declare expected and end_when predicates")
    if profile is not None:
        if profile not in config.profiles:
            raise ValueError(f"unknown profile: {profile}")
        config = config.model_copy(
            update={
                "cast": {
                    actor: ParticipantConfig(adapter="llm", profile=profile) for actor in scenario.world.character_ids
                }
            }
        )
    cast = {actor: ParticipantConfig() for actor in scenario.world.character_ids}
    cast.update(scenario.cast)
    cast.update(config.cast)
    if set(cast) != set(scenario.world.character_ids) or any(binding.adapter != "llm" for binding in cast.values()):
        raise ValueError("live acceptance requires an LLM binding for every character")

    recorder = RunRecorder(scenario, root=root)
    try:
        participants = build_participants(scenario, config, registry, recorder=recorder)
    except Exception as exc:
        recorder.finalize(
            state=scenario.create_world().state,
            result=None,
            status="failed",
            error=type(exc).__name__,
        )
        raise
    runner = ActRunner(scenario, participants, registry, recorder=recorder)
    result = runner.run(rounds)
    report = _acceptance_report(scenario, runner, recorder, result, mode="live")
    (recorder.run_dir / "acceptance.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _acceptance_report(scenario, runner, recorder, result: RunResult, *, mode: str) -> AcceptanceReport:
    expected = tuple(Fluents(runner.harness.world).satisfies(atom) for atom in scenario.expected)
    replay = replay_run(recorder.run_dir)
    return AcceptanceReport(
        success=result.status == "completed" and result.goals_met and all(expected) and replay.success,
        mode=mode,
        run_dir=str(recorder.run_dir),
        result=result,
        expected=expected,
        replay_matches=replay.success,
    )
