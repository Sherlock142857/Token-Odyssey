"""Local commands for compilation, play, acceptance checks and log playback."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import typer
from rich.console import Console

from token_odyssey import __version__
from token_odyssey.config.models import RunConfig, load_run_config
from token_odyssey.constants import (
    DEFAULT_ACT_LLM_TIMEOUT_SECONDS,
    DEFAULT_CAMPAIGN_LLM_TIMEOUT_SECONDS,
    DEFAULT_LOOPBACK_PORT,
)
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.llm.contracts import ChatMessage, ChatRole, LLMRequest
from token_odyssey.perception.models import Observation
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.composition import build_backend, build_participants
from token_odyssey.runtime.runner import ActRunner
from token_odyssey.scenario import load_scenario
from token_odyssey.translators.language import render_fact

app = typer.Typer(name="token-odyssey", no_args_is_help=True, invoke_without_command=True)
console = Console()
DEFAULT_SCENARIO = Path("scenarios/floodgate_dispatch.yaml")


@app.callback()
def root_command(
    version: Annotated[bool, typer.Option("--version", help="显示版本并退出。", is_eager=True)] = False,
) -> None:
    """Token Odyssey 本地 World Harness 命令行。"""

    if version:
        console.print(f"token-odyssey {__version__}")
        raise typer.Exit()


def _warn_api_cost() -> None:
    console.print("[yellow]注意：此命令会或可能调用模型 API 并产生费用；运行类命令还会保存完整模型交互记录。[/yellow]")


@app.command("web")
def web_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", "-s", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    run_config_path: Annotated[Path | None, typer.Option("--run-config", exists=True, dir_okay=False)] = None,
    port: Annotated[int, typer.Option(min=1, max=65535)] = DEFAULT_LOOPBACK_PORT,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
    llm_timeout: Annotated[float, typer.Option("--llm-timeout", min=1)] = DEFAULT_ACT_LLM_TIMEOUT_SECONDS,
) -> None:
    """在 localhost 启动人类 / LLM 共同参与的单 act 测试页。"""
    from token_odyssey.interfaces.web.server import serve

    if run_config_path is not None:
        _warn_api_cost()
    scenario = load_scenario(scenario_path)
    config = load_run_config(run_config_path) if run_config_path else RunConfig()
    serve(scenario, config, port=port, runs_dir=runs_dir, llm_timeout=llm_timeout)


@app.command("play")
def play_command(
    run_config_path: Annotated[Path, typer.Option("--run-config", exists=True, dir_okay=False)],
    port: Annotated[int, typer.Option(min=1, max=65535)] = DEFAULT_LOOPBACK_PORT,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
    llm_timeout: Annotated[float, typer.Option("--llm-timeout", min=1)] = DEFAULT_CAMPAIGN_LLM_TIMEOUT_SECONDS,
    resume: Annotated[Path | None, typer.Option("--resume", exists=True, file_okay=False)] = None,
) -> None:
    """在 localhost 启动导演驱动的完整多 act 游玩。"""
    from token_odyssey.interfaces.campaign_web.server import serve

    _warn_api_cost()
    config = load_run_config(run_config_path)
    if config.campaign is None:
        raise typer.BadParameter("run config requires a campaign section")
    serve(config, port=port, runs_dir=runs_dir, llm_timeout=llm_timeout, resume=resume)


@app.command("validate")
def validate_command(
    scenario_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
) -> None:
    scenario = load_scenario(scenario_path)
    console.print(
        f"有效：{scenario.title}；{len(scenario.world.entities)} 个实体，"
        f"{len(scenario.world.passages)} 条通道，{len(scenario.world.mechanics)} 条机关规则。"
    )


@app.command("run")
def run_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", "-s", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    run_config_path: Annotated[Path | None, typer.Option("--run-config", exists=True, dir_okay=False)] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=1)] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    player_view: Annotated[str | None, typer.Option("--player-view")] = None,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
) -> None:
    """运行一个 Act；提供含 LLM cast 的配置时会调用模型 API。"""

    if run_config_path is not None:
        _warn_api_cost()
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

        def display(observation: Observation) -> None:
            original(observation)
            if observation.observer_id == player_view:
                labels.update(observation.labels)
                labels.update({e.id: e.name for e in observation.entities})
                if observation.source == "event":
                    for fact in observation.facts:
                        console.print(render_fact(fact, labels), markup=False)

        runner.observation.on_observation = display
    result = runner.run(rounds)
    console.print(
        f"{result.status}：{result.turns_completed} 次行动权，{result.transactions} 次事务，{result.events} 条事件。"
    )
    console.print(f"运行记录：{recorder.run_dir}")


@app.command("selftest")
def selftest_command(
    scenario_path: Annotated[Path, typer.Option("--scenario", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    mode: Annotated[str, typer.Option("--mode", help="all / scripted / translated")] = "all",
    runs_dir: Annotated[
        Path | None,
        typer.Option("--runs-dir", help="保留验收产物；省略时使用并清理临时目录。"),
    ] = None,
) -> None:
    """离线验证安装、翻译器、世界运行与回放；永不读取 API 配置。"""

    # Import here to keep verification independent from CLI initialization.
    from token_odyssey.verification import run_acceptance

    if mode not in {"all", "scripted", "translated"}:
        raise typer.BadParameter("mode must be all, scripted, or translated")

    def execute(root: Path) -> None:
        reports = [
            run_acceptance(scenario_path, root=root, mode=selected)
            for selected in (("scripted", "translated") if mode == "all" else (mode,))
        ]
        for report in reports:
            location = f"，记录={report.run_dir}" if runs_dir is not None else ""
            console.print(
                f"{report.mode}: {'通过' if report.success else '失败'}，回放={report.replay_matches}{location}"
            )
        if not all(report.success for report in reports):
            raise typer.Exit(1)

    if runs_dir is not None:
        execute(runs_dir)
    else:
        with TemporaryDirectory(prefix="token-odyssey-selftest-") as temporary:
            execute(Path(temporary))


@app.command("verify-live")
def verify_live_command(
    run_config_path: Annotated[Path, typer.Option("--run-config", exists=True, dir_okay=False)],
    scenario_path: Annotated[Path, typer.Option("--scenario", exists=True, dir_okay=False)] = DEFAULT_SCENARIO,
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    rounds: Annotated[int | None, typer.Option("--rounds", min=1)] = None,
    runs_dir: Annotated[Path, typer.Option("--runs-dir")] = Path("runs"),
) -> None:
    """使用真实 LLM 运行全角色 Act，并检查终态和回放。"""

    from token_odyssey.verification import run_live_acceptance

    _warn_api_cost()
    try:
        report = run_live_acceptance(
            scenario_path,
            load_run_config(run_config_path),
            root=runs_dir,
            rounds=rounds,
            profile=profile,
        )
    except Exception as exc:
        console.print(f"真实 API 验收未完成：{type(exc).__name__}")
        raise typer.Exit(1) from None
    console.print(
        f"live: {'通过' if report.success else '失败'}；状态={report.result.status}；"
        f"预期条件={sum(report.expected)}/{len(report.expected)}；回放={report.replay_matches}；记录={report.run_dir}"
    )
    if not report.success:
        raise typer.Exit(1)


@app.command("replay")
def replay_command(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    report = replay_run(run_dir)
    console.print(
        f"回放={'通过' if report.success else '失败'}，事务={report.transactions}，"
        f"事件={report.events}，角色视图={len(report.views)}"
    )
    if not report.success:
        raise typer.Exit(1)


@app.command("test-connection")
def test_connection(
    run_config_path: Annotated[Path, typer.Option("--run-config", exists=True, dir_okay=False)],
    profile_name: Annotated[str, typer.Option("--profile")],
) -> None:
    """执行一次真实模型请求以验证 OpenAI-compatible 配置。"""

    _warn_api_cost()
    config = load_run_config(run_config_path)
    if profile_name not in config.profiles:
        raise typer.BadParameter("unknown profile")
    profile = config.profiles[profile_name]
    backend = build_backend(config.backends[profile.backend_id])
    response = backend.complete(
        LLMRequest(
            profile=profile,
            messages=[
                ChatMessage(role=ChatRole.SYSTEM, content="Return only a JSON object."),
                ChatMessage(role=ChatRole.USER, content='Return {"status":"ok"}.'),
            ],
        )
    )
    if not response.content:
        raise typer.BadParameter("backend returned empty content")
    console.print(f"连接成功：{profile.backend_id} / {response.model or profile.model}")
