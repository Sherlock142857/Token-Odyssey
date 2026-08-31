"""Terminal interface for scenario validation, connection checks and Act runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel

from airpg.agents import DeepSeekAgent, DemoAgent
from airpg.debug import ConsoleDebugSink, NullDebugSink
from airpg.engine import GameEngine
from airpg.models import Observation, WorldEvent
from airpg.recording import RunRecorder
from airpg.replay import replay_run
from airpg.scenario import load_scenario, read_api_key


app = typer.Typer(
    name="airpg",
    help="Language-driven RPG world harness",
    no_args_is_help=True,
)
console = Console()


@app.command("replay")
def replay_command(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, readable=True, help="Recorded run directory"),
    ],
) -> None:
    """Replay recorded model decisions and verify deterministic world results."""
    report = replay_run(run_dir)
    color = "green" if report.success else "red"
    console.print(
        Panel.fit(
            f"events={report.event_count}\n"
            f"events_match={report.events_match}\n"
            f"final_state_matches={report.final_state_matches}",
            title="Replay",
            border_style=color,
        )
    )
    if not report.success:
        raise typer.Exit(code=1)


@app.command("validate")
def validate_command(
    scenario_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="YAML scenario path"),
    ] = Path("scenarios/rainy_night.yaml"),
) -> None:
    """Validate a scenario and all world references without running an agent."""
    scenario = load_scenario(scenario_path)
    console.print(
        Panel.fit(
            f"[green]有效[/green]  {scenario.title}\n"
            f"rooms={len(scenario.world.rooms)}  actors={len(scenario.world.actors)}  "
            f"items={len(scenario.world.items)}  default_rounds={scenario.max_rounds}",
            title="scenario",
        )
    )


@app.command("test-connection")
def test_connection(
    api_key_file: Annotated[
        Path,
        typer.Option("--api-key-file", exists=True, dir_okay=False, readable=True),
    ] = Path("api.txt"),
    base_url: Annotated[str, typer.Option("--base-url")] = "https://api.deepseek.com",
    model: Annotated[str, typer.Option("--model")] = "deepseek-v4-flash",
) -> None:
    """Make one minimal completion to verify the configured OpenAI-compatible API."""
    client = OpenAI(api_key=read_api_key(api_key_file), base_url=base_url)
    with console.status(f"正在测试 {model} …"):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return only a JSON object."},
                {"role": "user", "content": 'Return {"status":"ok"}.'},
            ],
            max_tokens=128,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
    if not response.choices or not response.choices[0].message.content:
        raise typer.BadParameter("连接成功但模型没有返回内容")
    console.print(f"[green]连接成功[/green]  base_url={base_url}  model={model}")


@app.command("run")
def run_command(
    scenario_path: Annotated[
        Path,
        typer.Option("--scenario", "-s", exists=True, dir_okay=False, readable=True),
    ] = Path("scenarios/rainy_night.yaml"),
    provider: Annotated[str, typer.Option("--provider", help="deepseek or demo")] = "deepseek",
    rounds: Annotated[int | None, typer.Option("--rounds", min=1)] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    api_key_file: Annotated[
        Path,
        typer.Option("--api-key-file", exists=True, dir_okay=False, readable=True),
    ] = Path("api.txt"),
    developer: Annotated[bool, typer.Option("--developer/--no-developer")] = False,
    developer_view: Annotated[
        str,
        typer.Option(
            "--developer-view",
            help="Comma-separated debug categories, or all",
        ),
    ] = "all",
    player_view: Annotated[
        str | None,
        typer.Option("--player-view", help="Stream only this actor's observations as a future player UI"),
    ] = None,
    world_log: Annotated[
        Path | None,
        typer.Option("--world-log", help="Write accepted canonical events as JSONL"),
    ] = None,
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Root directory for complete run records"),
    ] = Path("runs"),
) -> None:
    """Run one fixed Act for a bounded number of shuffled rounds."""
    scenario = load_scenario(scenario_path)
    categories = None
    if developer and developer_view != "all":
        categories = {part.strip() for part in developer_view.split(",") if part.strip()}
    debug = ConsoleDebugSink(console, categories) if developer else NullDebugSink()
    if provider == "deepseek":
        api_key = read_api_key(api_key_file)
        agents = {
            actor_id: DeepSeekAgent(
                api_key=api_key,
                base_url=actor.model.base_url,
                model=actor.model.model,
                temperature=actor.model.temperature,
                max_tokens=actor.model.max_tokens,
                thinking_enabled=actor.model.thinking_enabled,
            )
            for actor_id, actor in scenario.world.actors.items()
        }
    elif provider == "demo":
        demo = DemoAgent()
        agents = {actor_id: demo for actor_id in scenario.world.actors}
    else:
        raise typer.BadParameter("provider 必须是 deepseek 或 demo")

    if player_view is not None and player_view not in scenario.world.actors:
        raise typer.BadParameter(f"未知 player-view actor id: {player_view}")

    actual_seed = scenario.seed if seed is None else seed
    recorder = RunRecorder(
        scenario,
        seed=actual_seed,
        provider=provider,
        root=runs_dir,
    )
    engine = GameEngine(
        scenario,
        agents,
        seed=seed,
        debug=debug,
        recorder=recorder,
    )
    displayed_round = 0

    def stage(event: WorldEvent, text: str) -> None:
        nonlocal displayed_round
        if event.round_number != displayed_round:
            displayed_round = event.round_number
            console.print(f"\n[bold cyan]第 {displayed_round} 轮[/bold cyan]")
        actor = scenario.world.actors[event.actor_id]
        if event.kind.value == "dialogue":
            console.print(f"[bold]{actor.name}[/bold]：{event.data['content']}")
        else:
            console.print(f"[italic]{text}[/italic]")

    engine.add_world_event_listener(stage)
    if player_view is not None:
        def stream(observation: Observation) -> None:
            if observation.observer_id == player_view:
                style = "yellow" if observation.level.value == "partial" else "white"
                console.print(f"[{style}]{observation.text}[/{style}]")

        engine.add_observation_listener(stream)

    actual_rounds = scenario.max_rounds if rounds is None else rounds
    console.print(
        f"[bold]{scenario.title}[/bold] · provider={provider} · rounds={actual_rounds} · "
        f"seed={actual_seed}"
    )
    result = engine.run(actual_rounds)
    if world_log is not None:
        world_log.parent.mkdir(parents=True, exist_ok=True)
        with world_log.open("w", encoding="utf-8") as handle:
            for event in engine.harness.world_log:
                handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        console.print(f"World Log 已写入 {world_log}")
    console.print(
        Panel.fit(
            f"rounds={result.rounds_completed}\n"
            f"turns={result.turns_completed}\n"
            f"world_events={result.world_event_count}",
            title="Act run complete",
            border_style="green",
        )
    )
    console.print(f"完整运行记录：[cyan]{recorder.run_dir}[/cyan]")


if __name__ == "__main__":
    app()
