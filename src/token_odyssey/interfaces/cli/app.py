"""Token Odyssey v2 terminal interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from token_odyssey.agents.contracts import ChatMessage, ChatRole
from token_odyssey.config.models import demo_run_config, load_run_config
from token_odyssey.inside_act.actions import build_builtin_registry
from token_odyssey.inside_act.domain.events import EventSource, WorldEvent
from token_odyssey.inside_act.domain.knowledge import Observation
from token_odyssey.inside_act.router import ShuffledRoundRouter
from token_odyssey.inside_act.runner import ActRunner
from token_odyssey.interfaces.cli.composition import build_backend_registry, build_participants
from token_odyssey.llm.contracts import LLMRequest
from token_odyssey.llm.registry import LLMProfileRegistry
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.scenario import load_scenario


app = typer.Typer(name="token-odyssey", help="Deterministic observable theatrical RPG runtime", no_args_is_help=True)
console = Console()


@app.command("validate")
def validate_command(
    scenario_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)] = Path("scenarios/rainy_night.yaml"),
) -> None:
    scenario = load_scenario(scenario_path)
    console.print(
        Panel.fit(
            f"[green]有效[/green] {scenario.title}\n"
            f"entities={len(scenario.world.entities)} rooms={len(scenario.world.room_ids)} "
            f"characters={len(scenario.world.character_ids)} items={len(scenario.world.item_ids)}",
            title="Scenario v2",
        )
    )


@app.command("run")
def run_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", "-s", exists=True, dir_okay=False)] = Path("scenarios/rainy_night.yaml"),
    run_config_path: Annotated[Path | None, typer.Option("--run-config", exists=True, dir_okay=False)] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=1)] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    player_view: Annotated[str | None, typer.Option("--player-view")] = None,
    world_log: Annotated[Path | None, typer.Option("--world-log")] = None,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
) -> None:
    scenario = load_scenario(scenario_path)
    registry = build_builtin_registry()
    config = load_run_config(run_config_path) if run_config_path else demo_run_config(scenario.world.character_ids)
    participants = build_participants(scenario, config, registry)
    actual_seed = scenario.seed if seed is None else seed
    modes = {actor_id: entry.mode or entry.adapter for actor_id, entry in config.cast.items()}
    recorder = RunRecorder(scenario, seed=actual_seed, modes=modes, root=runs_dir)
    runner = ActRunner(
        scenario,
        participants,
        registry,
        ShuffledRoundRouter(actual_seed),
        seed=actual_seed,
        recorder=recorder,
    )
    if player_view is not None:
        if player_view not in scenario.world.character_ids:
            raise typer.BadParameter(f"未知 player-view Character id: {player_view}")

        def stream(observation: Observation) -> None:
            if observation.observer_id == player_view:
                console.print(observation.text)

        runner.add_observation_listener(stream)

    displayed_round = 0

    def stage(event: WorldEvent, text: str) -> None:
        nonlocal displayed_round
        if event.round_number != displayed_round:
            displayed_round = event.round_number
            console.print(f"\n[bold cyan]第 {displayed_round} 轮[/bold cyan]")
        console.print(
            text
            if event.source == EventSource.ACTION and event.action_kind == "say"
            else f"[italic]{text}[/italic]"
        )

    runner.add_world_event_listener(stage)
    actual_rounds = scenario.max_rounds if rounds is None else rounds
    result = runner.run(actual_rounds)
    if world_log is not None:
        world_log.parent.mkdir(parents=True, exist_ok=True)
        world_log.write_text(
            "".join(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n" for event in runner.harness.world_log),
            encoding="utf-8",
        )
    console.print(Panel.fit(f"rounds={result.rounds_completed}\nturns={result.turns_completed}\nevents={result.world_event_count}", title="Act complete"))
    console.print(f"运行记录：[cyan]{recorder.run_dir}[/cyan]")


@app.command("replay")
def replay_command(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    report = replay_run(run_dir)
    console.print(Panel.fit(f"events={report.event_count}\nevents_match={report.events_match}\nfinal_state_matches={report.final_state_matches}", title="Replay"))
    if not report.success:
        raise typer.Exit(code=1)


@app.command("test-connection")
def test_connection(
    run_config_path: Annotated[Path, typer.Option("--run-config", exists=True, dir_okay=False)],
    mode: Annotated[str, typer.Option("--mode")],
) -> None:
    config = load_run_config(run_config_path)
    profiles = LLMProfileRegistry(config.llm_profiles)
    profile = profiles.get(mode)
    backend = build_backend_registry(config).get(profile.backend_id)
    response = backend.complete(
        LLMRequest(
            profile=profile,
            messages=[
                ChatMessage(role=ChatRole.SYSTEM, content="Return only a JSON object."),
                ChatMessage(role=ChatRole.USER, content='Return {"status":"ok"}.'),
            ],
        )
    )
    console.print(f"[green]连接成功[/green] backend={profile.backend_id} model={response.model or profile.model}")
