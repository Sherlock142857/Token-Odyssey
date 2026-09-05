"""Reusable full-flow acceptance entry point, independent of pytest and networks."""

import json
from collections import deque
from pathlib import Path

from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.common import FrozenModel
from token_odyssey.runtime.composition import build_scripted_participants, identity_for
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.llm.contracts import LLMProfile, LLMResponse
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.runner import ActRunner, RunResult
from token_odyssey.scenario import load_scenario
from token_odyssey.translators.llm import LLMTranslator


class ScriptedResponseBackend:
    """Fake only the transport response; exercise the real translator and session."""

    def __init__(self, batches):
        self.responses = deque(json.dumps(raw, ensure_ascii=False) for raw in batches)

    def complete(self, request):
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
    registry = builtin_registry()
    scenario = load_scenario(scenario_path, registry)
    if not scenario.expected:
        raise ValueError("an acceptance scenario must declare expected predicates")
    recorder = RunRecorder(scenario, root=root)
    if mode == "scripted":
        participants = build_scripted_participants(scenario, registry)
    elif mode == "translated":
        participants = {
            actor: LLMAgent(actor, LLMTranslator(registry, identity_for(scenario, actor)),
                            ScriptedResponseBackend(scenario.scripts.get(actor, ())),
                            LLMProfile(backend_id="offline", model="scripted-response"),
                            on_exchange=lambda exchange: recorder.record("llm_exchanges", exchange))
            for actor in scenario.world.character_ids
        }
    else:
        raise ValueError("acceptance mode must be scripted or translated")
    runner = ActRunner(scenario, participants, registry, recorder=recorder)
    result = runner.run()
    expected = tuple(Fluents(runner.harness.world).satisfies(atom) for atom in scenario.expected)
    replay = replay_run(recorder.run_dir)
    report = AcceptanceReport(success=all(expected) and replay.success, mode=mode, run_dir=str(recorder.run_dir),
                              result=result, expected=expected, replay_matches=replay.success)
    (recorder.run_dir / "acceptance.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
