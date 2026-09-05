from collections import deque

import pytest

from conftest import FixedRouter, MemoryRecorder
from token_odyssey.agents.human import HumanAgent
from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.config.models import RunConfig, load_run_config
from token_odyssey.kernel.state import Placement
from token_odyssey.llm.contracts import LLMProfile, LLMResponse
from token_odyssey.runtime.composition import build_participants, build_scripted_participants, identity_for
from token_odyssey.runtime.runner import ActRunner
from token_odyssey.scenario import compile_scenario
from token_odyssey.translators.human import HumanTranslator
from token_odyssey.translators.llm import LLMTranslator


def scripted_runner(data, registry, order=("alice",)):
    scenario = compile_scenario(data)
    recorder = MemoryRecorder()
    router = FixedRouter(order)
    runner = ActRunner(scenario, build_scripted_participants(scenario, registry), registry, router=router, recorder=recorder)
    return runner, recorder, router


def test_successful_prefix_is_not_retried_after_later_failure(scenario_data, registry):
    scenario_data["roles"] = {"alice": {"known_entity_ids": ["gem", "eve"]}}
    scenario_data["scripts"] = {"alice": [{"actions": [
        {"kind": "unlock", "lockable_id": "box", "key_item_id": "key"},
        {"kind": "open", "openable_id": "box"},
        {"kind": "take", "item_id": "gem"},
        {"kind": "give", "item_id": "gem", "recipient_id": "eve"},
    ]}]}
    runner, recorder, _ = scripted_runner(scenario_data, registry)
    runner.step()
    assert [t.action_kind for t in runner.harness.world_log] == ["unlock", "open", "take"]
    assert len(recorder.records["decisions"]) == 1
    assert runner.harness.world.state.placements["gem"].parent_id == "alice"
    assert [i.code for i in runner.observation.memories["alice"].feedback] == ["NOT_COLOCATED", "BATCH_STOPPED"]


@pytest.mark.parametrize("continue_after_move", [False, True])
def test_move_policy_has_explicit_outcomes(scenario_data, registry, continue_after_move):
    scenario_data["initial_state"]["openings"] = {"gate": True}
    scenario_data["turn_policy"] = {"continue_after_move": continue_after_move}
    scenario_data["roles"] = {"alice": {"known_entity_ids": ["secret"]}}
    scenario_data["scripts"] = {"alice": [{"actions": [
        {"kind": "move", "destination_room_id": "b"}, {"kind": "take", "item_id": "secret"},
    ]}]}
    runner, _, _ = scripted_runner(scenario_data, registry)
    runner.step()
    assert runner.harness.world.room_of("alice") == "b"
    assert runner.harness.world.state.placements["secret"].parent_id == ("alice" if continue_after_move else "b")
    assert [t.action_kind for t in runner.harness.world_log] == (["move", "take"] if continue_after_move else ["move"])


def test_move_then_failed_interaction_still_preserves_move(scenario_data, registry):
    scenario_data["initial_state"]["openings"] = {"gate": True}
    scenario_data["initial_state"]["placements"]["secret"] = {"parent_id": "eve", "relation": "attached"}
    scenario_data["turn_policy"] = {"continue_after_move": True}
    scenario_data["roles"] = {"alice": {"known_entity_ids": ["secret"]}}
    scenario_data["scripts"] = {"alice": [{"actions": [
        {"kind": "move", "destination_room_id": "b"}, {"kind": "take", "item_id": "secret"},
    ]}]}
    runner, _, _ = scripted_runner(scenario_data, registry)
    runner.step()
    assert runner.harness.world.room_of("alice") == "b"
    assert [t.action_kind for t in runner.harness.world_log] == ["move"]


def test_discovery_does_not_authorize_guessed_ids_in_already_submitted_batch(scenario_data, registry):
    scenario_data["scripts"] = {"alice": [
        {"actions": [
            {"kind": "unlock", "lockable_id": "box", "key_item_id": "key"},
            {"kind": "open", "openable_id": "box"},
            {"kind": "search", "container_id": "box"},
            {"kind": "take", "item_id": "gem"},
        ]},
        {"actions": [{"kind": "take", "item_id": "gem"}]},
    ]}
    runner, _, _ = scripted_runner(scenario_data, registry)
    runner.step()
    assert "gem" in runner.observation.known_ids("alice")
    assert runner.harness.world.state.placements["gem"].parent_id == "box"
    assert runner.observation.memories["alice"].feedback[0].code == "UNKNOWN_TO_ACTOR"
    runner.step()
    assert runner.harness.world.state.placements["gem"].parent_id == "alice"


def test_router_receives_committed_interaction_before_next_selection(scenario_data, registry):
    scenario_data["scripts"] = {"alice": [{"actions": [{"kind": "give", "item_id": "key", "recipient_id": "bob"}]}]}
    runner, _, router = scripted_runner(scenario_data, registry, order=("alice", "bob"))
    runner.step()
    runner.step()
    assert len(router.received_events[1]) == 1
    assert router.received_events[1][0].data["recipient_id"] == "bob"


def test_human_input_pauses_without_rescanning_or_advancing(scenario, registry):
    recorder, router = MemoryRecorder(), FixedRouter(["alice"])
    participants = build_scripted_participants(scenario, registry)
    human = HumanAgent("alice", HumanTranslator(registry))
    participants["alice"] = human
    runner = ActRunner(scenario, participants, registry, recorder=recorder, router=router)
    result = runner.run(1)
    assert result.status == "waiting_for_input" and result.turns_completed == 0
    presented = human.present()
    assert "world" not in presented and "flags" not in presented["view"]
    observed_count = len(runner.observation.log)
    assert runner.step() == "waiting_for_input"
    assert len(runner.observation.log) == observed_count and router.index == 1
    with pytest.raises(ValueError, match="stale"):
        human.submit("old-request", [{"kind": "wait"}])
    human.submit(presented["request_id"], [{"kind": "unlock", "lockable_id": "box", "key_item_id": "key"}])
    assert runner.step() == "turn_completed"
    assert runner.turns_completed == 1 and not runner.harness.world.state.locks["box"]
    with pytest.raises(ValueError):
        human.submit(presented["request_id"], [{"kind": "wait"}])


class Responses:
    def __init__(self, responses):
        self.responses, self.requests = deque(responses), []

    def complete(self, request):
        self.requests.append(request)
        return LLMResponse(content=self.responses.popleft())


def test_model_translation_and_repair_are_separate_from_kernel(scenario, registry):
    backend = Responses([
        '{"actions":[{"kind":"give","item_id":"key","target_ids":["bob"]}]}',
        '{"actions":[{"kind":"give","item_id":"key","recipient_id":"bob"}]}',
    ])
    recorder = MemoryRecorder()
    participants = build_scripted_participants(scenario, registry)
    participants["alice"] = LLMAgent("alice", LLMTranslator(registry, identity_for(scenario, "alice")), backend,
                                     LLMProfile(backend_id="fake", model="fake"),
                                     on_exchange=lambda e: recorder.record("llm_exchanges", e))
    runner = ActRunner(scenario, participants, registry, router=FixedRouter(["alice"]), recorder=recorder)
    runner.step()
    assert len(backend.requests) == 2
    assert "[当前物品]" in backend.requests[0].messages[-1].content
    assert '"items":' not in backend.requests[0].messages[-1].content
    assert "未执行任何动作" in backend.requests[1].messages[-1].content
    assert "recipient_id" in backend.requests[1].messages[-1].content
    assert "远处物品" not in backend.requests[0].messages[-1].content
    assert [t.action_kind for t in runner.harness.world_log] == ["give"]
    assert len(recorder.records["llm_exchanges"]) == 2
    decision = recorder.records["decisions"][-1]["decision"]
    assert not hasattr(decision, "raw_content") and not hasattr(decision, "usage")


def test_malformed_later_action_prevents_shape_valid_prefix_execution(scenario, registry):
    backend = Responses(['{"actions":[{"kind":"give","item_id":"key","recipient_id":"bob"},{"kind":"not_real"}]}'] * 3)
    participants = build_scripted_participants(scenario, registry)
    participants["alice"] = LLMAgent("alice", LLMTranslator(registry, identity_for(scenario, "alice")), backend,
                                     LLMProfile(backend_id="fake", model="fake"))
    runner = ActRunner(scenario, participants, registry, router=FixedRouter(["alice"]))
    runner.step()
    assert [t.action_kind for t in runner.harness.world_log] == ["wait"]
    assert runner.harness.world.state.placements["key"].parent_id == "alice"


def test_per_actor_profiles_and_private_briefs(scenario_data, registry, monkeypatch):
    scenario_data["roles"] = {"alice": {"private_goal": "ALICE_ONLY"}, "bob": {"private_goal": "BOB_ONLY"}}
    scenario_data["cast"] = {"alice": {"adapter": "llm", "profile": "fast"}, "bob": {"adapter": "llm", "profile": "careful"}}
    scenario = compile_scenario(scenario_data)
    config = RunConfig.model_validate({"backends": {"test": {"driver": "fake", "base_url": "http://unused", "api_key_env": "UNUSED"}},
                                       "profiles": {"fast": {"backend_id": "test", "model": "model-a"},
                                                    "careful": {"backend_id": "test", "model": "model-b"}}})
    backend = Responses(['{"actions":[{"kind":"wait"}]}'] * 2)
    monkeypatch.setitem(__import__("token_odyssey.runtime.composition", fromlist=["BACKEND_FACTORIES"]).BACKEND_FACTORIES,
                        "fake", lambda config: backend)
    participants = build_participants(scenario, config, registry)
    runner = ActRunner(scenario, participants, registry, router=FixedRouter(["alice", "bob"]))
    runner.step()
    runner.step()
    assert [r.profile.model for r in backend.requests] == ["model-a", "model-b"]
    assert "BOB_ONLY" not in backend.requests[0].messages[0].content
    assert "ALICE_ONLY" not in backend.requests[1].messages[0].content


def test_key_file_is_resolved_relative_to_config(tmp_path):
    path = tmp_path / "api.yaml"
    path.write_text('schema_version: 3\nbackends:\n  main:\n    base_url: http://unused\n    api_key_file: secret.txt\n')
    config = load_run_config(path)
    assert config.backends["main"].api_key_file == str(tmp_path / "secret.txt")
