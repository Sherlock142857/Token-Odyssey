import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import ROOT
from token_odyssey.interfaces.cli.app import app
from token_odyssey.recording.replay import replay_run
from token_odyssey.verification import run_acceptance

SCENARIO = ROOT / "scenarios/sealed_chalice.yaml"


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
    assert any(o["observer_id"] == "witness" and any(f["kind"] == "mechanism_heard" for f in o["facts"])
               for o in observations)
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


def test_cli_validation_and_offline_acceptance(tmp_path):
    cli = CliRunner()
    validation = cli.invoke(app, ["validate", str(SCENARIO)])
    assert validation.exit_code == 0, validation.output
    check = cli.invoke(app, ["selftest", "--scenario", str(SCENARIO), "--runs-dir", str(tmp_path)])
    assert check.exit_code == 0, check.output
    assert "scripted" in check.output and "translated" in check.output
