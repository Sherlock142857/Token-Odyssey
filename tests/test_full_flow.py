import json
from pathlib import Path

import pytest
from conftest import ROOT
from typer.testing import CliRunner

from token_odyssey.config.models import RunConfig
from token_odyssey.interfaces.cli.app import app
from token_odyssey.orchestration.models import CampaignState
from token_odyssey.recording.replay import replay_run
from token_odyssey.verification import run_acceptance

SCENARIO = ROOT / "tests/fixtures/scenarios/sealed_chalice.yaml"


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.mark.parametrize("mode", ["scripted", "translated"])
def test_complete_new_act_with_intentional_failure_and_log_playback(tmp_path, mode, monkeypatch):
    report = run_acceptance(SCENARIO, root=tmp_path, mode=mode)
    assert report.success and report.result.goals_met and all(report.expected)
    path = Path(report.run_dir)
    transactions = rows(path / "transactions.jsonl")
    chain = next(t for t in transactions if t["action_kind"] == "place")
    assert [e["kind"] for e in chain["events"]] == ["place", "mechanism", "mechanism"]
    failed = [r for r in rows(path / "action_results.jsonl") if not r["accepted"]]
    assert len(failed) == 1 and failed[0]["kind"] == "take"
    assert failed[0]["issues"][0]["code"] == "CLOSED_CONTAINER_BLOCKS_ACCESS"
    assert not (path / "fallbacks.jsonl").exists()
    observations = rows(path / "observations.jsonl")
    assert any(
        o["observer_id"] == "witness" and any(f["kind"] == "mechanism_heard" for f in o["facts"]) for o in observations
    )
    if mode == "translated":
        prompt = (path / "prompt_flow.md").read_text()
        assert "[当前物品]" in prompt
        assert "已成功" in prompt or "成功的动作保留" in prompt
        assert (path / "llm_exchanges.jsonl").exists()
    # Playback cannot consume RNG or call a model: it reads committed changes
    # and the actual subjective views which were recorded at decision time.
    import random

    monkeypatch.setattr(random.Random, "random", lambda _: pytest.fail("replay resampled perception"))
    replay = replay_run(path)
    assert replay.success
    assert [v.model_dump(mode="json") for v in replay.views] == rows(path / "views.jsonl")


def test_scripted_and_translated_modes_have_identical_worlds_and_views(tmp_path):
    direct = run_acceptance(SCENARIO, root=tmp_path, mode="scripted")
    translated = run_acceptance(SCENARIO, root=tmp_path, mode="translated")
    for file in ("transactions.jsonl", "observations.jsonl", "views.jsonl", "final_state.json"):
        assert (Path(direct.run_dir) / file).read_text() == (Path(translated.run_dir) / file).read_text()


def test_replay_detects_final_state_disagreement(tmp_path):
    report = run_acceptance(SCENARIO, root=tmp_path)
    path = Path(report.run_dir) / "final_state.json"
    state = json.loads(path.read_text())
    state["flags"]["beacon_active"] = False
    path.write_text(json.dumps(state))
    replay = replay_run(report.run_dir)
    assert not replay.success and not replay.final_state_matches


def test_replay_rejects_noncurrent_log_schema(tmp_path):
    report = run_acceptance(SCENARIO, root=tmp_path)
    manifest_path = Path(report.run_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="only run schema 4"):
        replay_run(report.run_dir)


def test_replay_detects_conflicting_perception_settings(tmp_path):
    report = run_acceptance(SCENARIO, root=tmp_path)
    path = Path(report.run_dir)
    events = rows(path / "events.jsonl")
    cue = next(c for event in events for c in event["cues"])
    cue["clear_in_room"] = not cue["clear_in_room"]
    (path / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n")
    replay = replay_run(path)
    assert not replay.success and not replay.events_match


def test_cli_validation_and_offline_acceptance(tmp_path):
    cli = CliRunner()
    version = cli.invoke(app, ["--version"])
    assert version.exit_code == 0 and "0.1.0" in version.output
    validation = cli.invoke(app, ["validate", str(SCENARIO)])
    assert validation.exit_code == 0, validation.output
    check = cli.invoke(app, ["selftest", "--scenario", str(SCENARIO), "--runs-dir", str(tmp_path)])
    assert check.exit_code == 0, check.output
    assert "scripted" in check.output and "translated" in check.output


@pytest.mark.parametrize(
    "command",
    ["validate", "run", "web", "play", "selftest", "replay", "test-connection", "verify-live"],
)
def test_stable_cli_commands_expose_help(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output


def test_selftest_is_offline_and_uses_temporary_artifacts(monkeypatch):
    monkeypatch.setenv("TOKEN_ODYSSEY_API_KEY", "must-not-be-read")
    monkeypatch.setattr(
        "token_odyssey.llm.providers.openai_compatible.OpenAICompatibleBackend.__init__",
        lambda *args, **kwargs: pytest.fail("selftest constructed a network backend"),
    )
    result = CliRunner().invoke(app, ["selftest"])
    assert result.exit_code == 0, result.output
    assert "scripted" in result.output and "translated" in result.output
    assert "记录=" not in result.output


def test_verify_live_warns_about_cost_and_rejects_non_llm_cast(tmp_path):
    config = tmp_path / "offline.yaml"
    config.write_text("schema_version: 3\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["verify-live", "--run-config", str(config)])
    assert result.exit_code == 1
    assert "API" in result.output and "费用" in result.output


def test_noncurrent_config_and_checkpoint_schemas_are_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        RunConfig.model_validate({"schema_version": 2})
    with pytest.raises(ValueError, match="schema_version"):
        CampaignState.model_validate({"schema_version": 0, "campaign_id": "old"})


def test_floodgate_scripted_and_translated_run_cover_actions_and_match(tmp_path):
    scenario = ROOT / "scenarios/floodgate_dispatch.yaml"
    direct = run_acceptance(scenario, root=tmp_path, mode="scripted")
    translated = run_acceptance(scenario, root=tmp_path, mode="translated")
    assert direct.success and translated.success
    assert direct.result.status == translated.result.status == "completed"
    path = Path(direct.run_dir)
    actions = rows(path / "action_results.jsonl")
    assert all(row["accepted"] for row in actions)
    assert {row["kind"] for row in actions} == {
        "say",
        "give",
        "take",
        "open",
        "close",
        "lock",
        "unlock",
        "install",
        "operate",
        "search",
        "move",
        "wait",
    }
    assert any(r["actors"][r["actor_id"]]["impulse"] >= 4 for r in rows(path / "routing.jsonl"))
    for filename in ("transactions.jsonl", "observations.jsonl", "views.jsonl", "routing.jsonl", "final_state.json"):
        assert (path / filename).read_bytes() == (Path(translated.run_dir) / filename).read_bytes()
