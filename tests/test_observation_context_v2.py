from __future__ import annotations

from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.inside_act.actions import build_builtin_registry
from token_odyssey.inside_act.context import ContextProjector
from token_odyssey.inside_act.domain.events import AcceptedTurn
from token_odyssey.inside_act.domain.knowledge import AgentRuntime, ObservationLevel
from token_odyssey.inside_act.domain.spatial import Placement, RoomVisibilityGraph, WorldState
from token_odyssey.inside_act.harness import WorldHarness
from token_odyssey.inside_act.observation import ObservationSystem


class FixedRandom:
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


def make_system(scenario, registry, roll=0.1, trace=None):
    runtimes = {actor: AgentRuntime(actor_id=actor) for actor in scenario.world.character_ids}
    system = ObservationSystem(runtimes, registry, seed=1, trace_listener=trace)
    system.policy.rng = FixedRandom(roll)
    return system, runtimes


def test_environment_projection_separates_new_and_known_visible(scenario, registry):
    system, _ = make_system(scenario, registry)
    first = system.scan_environment(scenario.world, "shen_lan", 0)
    assert "ebony_box" in {view.id for view in first.newly_visible}
    assert "brass_key" not in {view.id for view in first.newly_visible}
    second = system.scan_environment(scenario.world, "shen_lan", 1)
    assert "ebony_box" in {view.id for view in second.known_visible}
    assert "ebony_box" not in {view.id for view in second.newly_visible}


def test_disappearing_item_only_updates_memory_and_emits_no_absence_text(scenario, registry):
    system, runtimes = make_system(scenario, registry, roll=0.1)
    system.scan_environment(scenario.world, "shen_lan", 0)
    state = scenario.world.model_copy(deep=True)
    state.placements["ebony_box"] = Placement(relation="inside", parent_id="study")
    state.room_graph = RoomVisibilityGraph(edges={})
    state.revision = 1
    state = WorldState.model_validate(state.model_dump(mode="python"))
    before = len(runtimes["shen_lan"].observations)
    system.policy.rng = FixedRandom(0.99)
    projection = system.scan_environment(state, "shen_lan", 1)
    assert "ebony_box" not in {view.id for view in [*projection.known_visible, *projection.newly_visible]}
    assert not runtimes["shen_lan"].knowledge.entities["ebony_box"].currently_observable
    new_text = "\n".join(obs.text for obs in runtimes["shen_lan"].observations[before:])
    assert "消失" not in new_text
    assert "不知道谁" not in new_text


def test_search_reveal_is_driven_by_directive_not_action_branch(scenario, registry):
    system, runtimes = make_system(scenario, registry, roll=0.1)
    system.scan_environment(scenario.world, "shen_lan", 0)
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        registry.parse_plan({"frames": [{"commands": [{"kind": "search", "target_entity_id": "ebony_box"}]}]}),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    frame = result.committed_frames[0]
    system.project_frame(frame)
    system.apply_directives(frame.after_state, frame.directives, 1)
    assert "brass_key" in runtimes["shen_lan"].knowledge.entities
    assert any("黄铜钥匙" in obs.text for obs in runtimes["shen_lan"].observations)


def test_direct_say_target_gets_full_content_while_other_observer_gets_partial(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["luo_wen"] = {"relation": "inside", "parent_id": "foyer"}
    state = WorldState.model_validate(raw)
    system, _ = make_system(scenario, registry, roll=0.15)
    harness = WorldHarness(state, registry)
    result = harness.resolve(
        "shen_lan",
        registry.parse_plan({"frames": [{"commands": [{
            "kind": "say", "amplitude": "subtle",
            "target_character_ids": ["qiao_man"], "content": "钥匙在盒子里",
        }]}]}),
        1,
    )
    frame = result.committed_frames[0]
    projected = system.project_frame(frame)[result.events[0].sequence]
    assert projected["qiao_man"].level == ObservationLevel.FULL
    assert "钥匙" in projected["qiao_man"].text
    assert projected["luo_wen"].level == ObservationLevel.PARTIAL
    assert "钥匙" not in projected["luo_wen"].text


def test_event_score_includes_action_visibility_and_amplitude(scenario, registry):
    traces = []
    system, _ = make_system(scenario, registry, roll=0.99, trace=lambda c, p: traces.append((c, p)))
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        registry.parse_plan({"frames": [{"commands": [{
            "kind": "hide", "amplitude": "subtle", "target_entity_id": "silver_ring"
        }]}]}),
        1,
    )
    # The action itself is invalid because Shen Lan does not control the ring.
    assert not isinstance(result, AcceptedTurn)
    valid = harness.resolve(
        "qiao_man",
        registry.parse_plan({"frames": [{"commands": [{
            "kind": "hide", "amplitude": "subtle", "target_entity_id": "silver_ring"
        }]}]}),
        1,
    )
    system.project_frame(valid.committed_frames[0])
    external = [p for c, p in traces if c == "event_projection" and p["observer_id"] == "shen_lan"][-1]
    assert external["score"] == 0.12  # same-room base 1 × hide .4 × subtle .3


def test_context_contains_only_projected_data_and_objective_language(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    environment = system.scan_environment(scenario.world, "shen_lan", 0)
    context = ContextProjector().build(scenario.world, runtimes["shen_lan"], environment, 1)
    assert context.actor_id == "shen_lan"
    assert "brass_key" not in {view.id for view in [*context.known_visible, *context.newly_visible]}
    all_text = "\n".join(observation.text for observation in context.new_observations)
    assert "不知道谁" not in all_text
    assert "可疑" not in all_text


def test_rendered_incremental_context_has_no_round_empty_sections_or_repeated_details(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    first_environment = system.scan_environment(scenario.world, "shen_lan", 0)
    first_context = ContextProjector().build(
        scenario.world, runtimes["shen_lan"], first_environment, 1
    )
    first_prompt = LLMAgent._render_context(first_context)
    assert "第 1 轮" not in first_prompt
    assert "/full" not in first_prompt
    assert "- 无" not in first_prompt
    assert scenario.world.entities["ebony_box"].description in first_prompt

    second_environment = system.scan_environment(scenario.world, "shen_lan", 1)
    second_context = ContextProjector().build(
        scenario.world, runtimes["shen_lan"], second_environment, 2
    )
    second_prompt = LLMAgent._render_context(second_context)
    assert "第 2 轮" not in second_prompt
    assert "当前仍可观察的已知实体索引" in second_prompt
    assert "ebony_box" in second_prompt
    assert scenario.world.entities["ebony_box"].description not in second_prompt
