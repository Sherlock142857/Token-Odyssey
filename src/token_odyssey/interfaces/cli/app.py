"""Local commands for compilation, play, acceptance checks and log playback."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from token_odyssey.config.models import RunConfig, load_run_config
from token_odyssey.runtime.composition import build_backend, build_participants
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.llm.contracts import ChatMessage, ChatRole, LLMRequest
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.runner import ActRunner
from token_odyssey.scenario import load_scenario
from token_odyssey.translators.language import render_fact

app = typer.Typer(name="token-odyssey", no_args_is_help=True)
console = Console()
DEFAULT_SCENARIO = Path("scenarios/floodgate_dispatch.yaml")


@app.command("web")
def web_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", "-s", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    run_config_path: Annotated[Path | None, typer.Option("--run-config", exists=True, dir_okay=False)] = None,
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
    llm_timeout: Annotated[float, typer.Option("--llm-timeout", min=1)] = 60,
):
    """在 localhost 启动人类 / LLM 共同参与的单 act 测试页。"""
    from token_odyssey.interfaces.web.server import serve
    scenario = load_scenario(scenario_path)
    config = load_run_config(run_config_path) if run_config_path else RunConfig()
    serve(scenario, config, port=port, runs_dir=runs_dir, llm_timeout=llm_timeout)


@app.command("validate")
def validate_command(scenario_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)] = DEFAULT_SCENARIO):
    scenario = load_scenario(scenario_path)
    console.print(f"有效：{scenario.title}；{len(scenario.world.entities)} 个实体，"
                  f"{len(scenario.world.passages)} 条通道，{len(scenario.world.mechanics)} 条机关规则。")


@app.command("run")
def run_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", "-s", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    run_config_path: Annotated[Path | None, typer.Option("--run-config", exists=True, dir_okay=False)] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=1)] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    player_view: Annotated[str | None, typer.Option("--player-view")] = None,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
):
    scenario = load_scenario(scenario_path)
    if player_view is not None and player_view not in scenario.world.character_ids:
        raise typer.BadParameter("player-view must be a Character ID")
    registry = builtin_registry()
    config = load_run_config(run_config_path) if run_config_path else RunConfig()
    recorder = RunRecorder(scenario, root=runs_dir, seed=seed)
    participants = build_participants(scenario, config, registry, recorder=recorder)
    runner = ActRunner(scenario, participants, registry, seed=seed, recorder=recorder)
    if player_view:
        original = runner.observation.on_observation
        labels = {}

        def display(observation):
            original(observation)
            if observation.observer_id == player_view:
                labels.update(observation.labels)
                labels.update({e.id: e.name for e in observation.entities})
                if observation.source == "event":
                    for fact in observation.facts:
                        console.print(render_fact(fact, labels), markup=False)
        runner.observation.on_observation = display
    result = runner.run(rounds)
    console.print(f"{result.status}：{result.turns_completed} 次行动权，{result.transactions} 次事务，{result.events} 条事件。")
    console.print(f"运行记录：{recorder.run_dir}")


@app.command("selftest")
def selftest_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    mode: Annotated[str, typer.Option("--mode", help="all / scripted / translated")] = "all",
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
):
    # Import here to keep verification independent from CLI initialization.
    from token_odyssey.verification import run_acceptance
    if mode not in {"all", "scripted", "translated"}:
        raise typer.BadParameter("mode must be all, scripted, or translated")
    reports = [run_acceptance(scenario_path, root=runs_dir, mode=selected)
               for selected in (("scripted", "translated") if mode == "all" else (mode,))]
    for report in reports:
        console.print(f"{report.mode}: {'通过' if report.success else '失败'}，回放={report.replay_matches}，{report.run_dir}")
    if not all(report.success for report in reports):
        raise typer.Exit(1)


@app.command("replay")
def replay_command(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]):
    report = replay_run(run_dir)
    console.print(f"回放={'通过' if report.success else '失败'}，事务={report.transactions}，事件={report.events}，角色视图={len(report.views)}")
    if not report.success:
        raise typer.Exit(1)


@app.command("test-connection")
def test_connection(
    run_config_path: Annotated[Path, typer.Option("--run-config", exists=True, dir_okay=False)],
    profile_name: Annotated[str, typer.Option("--profile")],
):
    config = load_run_config(run_config_path)
    if profile_name not in config.profiles:
        raise typer.BadParameter("unknown profile")
    profile = config.profiles[profile_name]
    backend = build_backend(config.backends[profile.backend_id])
    response = backend.complete(LLMRequest(profile=profile, messages=[
        ChatMessage(role=ChatRole.SYSTEM, content="Return only a JSON object."),
        ChatMessage(role=ChatRole.USER, content='Return {"status":"ok"}.'),
    ]))
    if not response.content:
        raise typer.BadParameter("backend returned empty content")
    console.print(f"连接成功：{profile.backend_id} / {response.model or profile.model}")
