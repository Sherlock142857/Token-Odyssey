"""Run real LLM participants and verify the scenario's final state and replay."""

import argparse
from pathlib import Path

from token_odyssey.config.models import ParticipantConfig, load_run_config
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.composition import build_participants
from token_odyssey.runtime.runner import ActRunner
from token_odyssey.scenario import load_scenario
from token_odyssey.verification import AcceptanceReport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("scenarios/floodgate_dispatch.yaml"))
    parser.add_argument("--run-config", type=Path, default=Path("configs/llm.deepseek.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--rounds", type=int, help="Override the scenario's round budget")
    args = parser.parse_args()
    if args.rounds is not None and args.rounds < 1:
        parser.error("--rounds must be positive")

    registry = builtin_registry()
    scenario = load_scenario(args.scenario, registry)
    config = load_run_config(args.run_config)
    if not scenario.expected or not scenario.end_when:
        parser.error("scenario must declare both expected and end_when")
    cast = {actor: ParticipantConfig() for actor in scenario.world.character_ids}
    cast.update(scenario.cast)
    cast.update(config.cast)
    if set(cast) != set(scenario.world.character_ids) or any(binding.adapter != "llm" for binding in cast.values()):
        parser.error("real API acceptance requires an LLM binding for every character")

    recorder = RunRecorder(scenario, root=args.runs_dir)
    print(f"真实 API 全流程；运行记录：{recorder.run_dir}", flush=True)
    try:
        participants = build_participants(scenario, config, registry, recorder=recorder)
    except Exception as exc:
        recorder.finalize(state=scenario.create_world().state, result=None, status="failed", error=type(exc).__name__)
        raise
    runner = ActRunner(scenario, participants, registry, recorder=recorder)
    result = runner.run(args.rounds)
    expected = tuple(Fluents(runner.harness.world).satisfies(atom) for atom in scenario.expected)
    replay = replay_run(recorder.run_dir)
    report = AcceptanceReport(
        success=result.status == "completed" and result.goals_met and all(expected) and replay.success,
        mode="live",
        run_dir=str(recorder.run_dir),
        result=result,
        expected=expected,
        replay_matches=replay.success,
    )
    (recorder.run_dir / "acceptance.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"live: {'通过' if report.success else '失败'}；状态={result.status}；"
          f"预期条件={sum(expected)}/{len(expected)}；回放={replay.success}")
    for atom, passed in zip(scenario.expected, expected):
        if not passed:
            print(f"未满足：{atom.model_dump_json(exclude_none=True)}")
    return 0 if report.success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Keep credentials and provider response bodies out of terminal errors.
        print(f"真实 API 验收未完成：{type(exc).__name__}")
        raise SystemExit(1) from None
