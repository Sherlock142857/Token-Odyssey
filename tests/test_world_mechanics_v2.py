from __future__ import annotations

import json

from token_odyssey.agents import ScriptedAgent
from token_odyssey.inside_act.domain.events import AcceptedTurn, EventSource, RejectedTurn
from token_odyssey.inside_act.domain.knowledge import AgentRuntime, ObservationLevel
from token_odyssey.inside_act.domain.spatial import WorldState
from token_odyssey.inside_act.harness import WorldHarness
from token_odyssey.inside_act.mechanics import render_world_event
from token_odyssey.inside_act.observation import ObservationSystem
from token_odyssey.inside_act.router import ShuffledRoundRouter
from token_odyssey.inside_act.runner import ActRunner
from token_odyssey.interfaces.cli.composition import _identity
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run


def plan(registry, actions):
    return registry.parse_plan({"actions": actions})


def test_install_rules_validate_compatibility_and_block_place_bypass(
    relay_scenario, registry
):
    harness = WorldHarness(relay_scenario.world, registry)
    known = {"tuning_leaf", "relay_console", "verification_tray"}

    incompatible = harness.resolve(
        "lu_yuan",
        plan(registry, [{
            "kind": "install",
            "component_id": "tuning_leaf",
            "target_id": "verification_tray",
        }]),
        1,
        known_entity_ids=known,
    )
    assert isinstance(incompatible, RejectedTurn)
    assert "不能安装" in incompatible.validation_issues[0].message

    bypass = harness.resolve(
        "lu_yuan",
        plan(registry, [{
            "kind": "place",
            "item_id": "tuning_leaf",
            "target_id": "relay_console",
            "relation": "attached",
        }]),
        1,
        known_entity_ids=known,
    )
    assert isinstance(bypass, RejectedTurn)
    assert "install" in bypass.validation_issues[0].message

    unknown = harness.resolve(
        "lu_yuan",
        plan(registry, [{
            "kind": "install",
            "component_id": "charged_battery",
            "target_id": "relay_console",
        }]),
        1,
        known_entity_ids={"relay_console"},
    )
    assert isinstance(unknown, RejectedTurn)
    assert unknown.validation_issues[0].code == "unknown_to_actor"

    raw = relay_scenario.world.model_dump(mode="python")
    raw["placements"]["tuning_leaf"] = {
        "relation": "attached",
        "parent_id": "xia_mang",
    }
    controlled_by_other = WorldHarness(WorldState.model_validate(raw), registry).resolve(
        "lu_yuan",
        plan(registry, [{
            "kind": "install",
            "component_id": "tuning_leaf",
            "target_id": "relay_console",
        }]),
        1,
        known_entity_ids={"tuning_leaf", "relay_console"},
    )
    assert isinstance(controlled_by_other, RejectedTurn)
    assert "没有控制" in controlled_by_other.validation_issues[0].message


def test_install_then_operate_emits_ordered_world_success(relay_scenario, registry):
    raw = relay_scenario.world.model_dump(mode="python")
    raw["placements"]["charged_battery"] = {
        "relation": "attached",
        "parent_id": "lu_yuan",
    }
    state = WorldState.model_validate(raw)
    harness = WorldHarness(state, registry)
    known = {"frequency_card", "tuning_leaf", "charged_battery", "relay_console"}
    result = harness.resolve(
        "lu_yuan",
        plan(registry, [
            {
                "kind": "install",
                "component_id": "tuning_leaf",
                "target_id": "relay_console",
            },
            {
                "kind": "install",
                "component_id": "charged_battery",
                "target_id": "relay_console",
            },
            {"kind": "operate", "target_id": "relay_console"},
        ]),
        1,
        known_entity_ids=known,
    )
    assert isinstance(result, AcceptedTurn)
    assert [event.source for event in result.events] == [
        EventSource.ACTION,
        EventSource.ACTION,
        EventSource.ACTION,
        EventSource.WORLD,
    ]
    assert result.events[-1].actor_id is None
    assert result.events[-1].trigger_actor_id == "lu_yuan"
    assert result.events[-1].mechanic_id == "relay_console_transmit"
    assert result.events[-1].data.outcome == "success"
    assert "三短一长" in render_world_event(
        result.final_state, result.events[-1], full=True
    )
    runtimes = {
        actor_id: AgentRuntime(actor_id=actor_id)
        for actor_id in result.final_state.character_ids
    }
    observation = ObservationSystem(runtimes, registry, seed=9)
    projected = observation.project_frame(result.committed_frames[-1])
    operator_view = projected[result.events[-1].sequence]["lu_yuan"]
    assert operator_view.level == ObservationLevel.FULL
    assert "三短一长" in operator_view.text


def test_operate_missing_components_is_accepted_world_failure(relay_scenario, registry):
    harness = WorldHarness(relay_scenario.world, registry)
    result = harness.resolve(
        "cheng_wu",
        plan(registry, [{"kind": "operate", "target_id": "relay_console"}]),
        1,
        known_entity_ids={"relay_console"},
    )
    assert isinstance(result, AcceptedTurn)
    assert result.events[-1].source == EventSource.WORLD
    assert result.events[-1].data.outcome == "failure"
    assert "指示灯没有亮起" in render_world_event(
        result.final_state, result.events[-1], full=True
    )


def test_world_mechanics_are_not_copied_into_character_identity(relay_scenario):
    identity = _identity(relay_scenario, "xia_mang")
    exposed = "\n".join(
        [identity.world_history, identity.act_background, identity.action_guidance]
    )
    operation = relay_scenario.world.mechanics.operations[0]
    assert operation.success.full_text not in exposed
    assert operation.failure.full_text not in exposed
    assert "校验盘原本就用于" not in identity.act_background


def test_duplicate_install_is_an_accepted_notice(relay_scenario, registry):
    harness = WorldHarness(relay_scenario.world, registry)
    result = harness.resolve(
        "xia_mang",
        plan(registry, [{
            "kind": "install",
            "component_id": "frequency_card",
            "target_id": "relay_console",
        }]),
        1,
        known_entity_ids={"frequency_card", "relay_console"},
    )
    assert isinstance(result, AcceptedTurn)
    assert result.events == ()
    assert result.execution_notices[0].code == "redundant_install"


def test_world_reaction_records_and_replays(relay_scenario, registry, wait_plan, tmp_path):
    operate = plan(registry, [{"kind": "operate", "target_id": "relay_console"}])
    scripts = {
        "xia_mang": [wait_plan()],
        "lu_yuan": [operate],
        "cheng_wu": [wait_plan()],
    }
    agent = ScriptedAgent(scripts, registry)
    recorder = RunRecorder(
        relay_scenario,
        seed=41,
        modes={actor: "test" for actor in relay_scenario.world.character_ids},
        root=tmp_path,
        run_id="world-reaction-replay",
    )
    runner = ActRunner(
        relay_scenario,
        {actor_id: agent for actor_id in relay_scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(41),
        seed=41,
        recorder=recorder,
    )
    runner.run(1)
    events = [
        json.loads(line)
        for line in (recorder.run_dir / "world_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [event["source"] for event in events] == ["action", "world"]
    report = replay_run(recorder.run_dir)
    assert report.success
    assert report.event_count == 2


def test_no_effect_world_event_records_and_replays(scenario, registry, wait_plan, tmp_path):
    operate = plan(registry, [{"kind": "operate", "target_id": "silver_ring"}])
    scripts = {
        "shen_lan": [wait_plan()],
        "qiao_man": [operate],
        "luo_wen": [wait_plan()],
    }
    agent = ScriptedAgent(scripts, registry)
    recorder = RunRecorder(
        scenario,
        seed=43,
        modes={actor: "test" for actor in scenario.world.character_ids},
        root=tmp_path,
        run_id="no-effect-replay",
    )
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(43),
        seed=43,
        recorder=recorder,
    )
    runner.run(1)
    events = [
        json.loads(line)
        for line in (recorder.run_dir / "world_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert events[-1]["data"] == {"outcome": "no_effect"}
    assert events[-1]["mechanic_id"] is None
    assert replay_run(recorder.run_dir).success
