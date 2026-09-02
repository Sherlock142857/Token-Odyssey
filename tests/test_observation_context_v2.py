from __future__ import annotations

from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.inside_act.actions import build_builtin_registry
from token_odyssey.inside_act.context import (
    ContextProjector,
    InteractionStatus,
    ScanObservationStatus,
)
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
    first_views = {view.id: view for view in first.full_observations}
    assert first_views["ebony_box"].observation_status == ScanObservationStatus.NEW
    assert "brass_key" not in first_views
    second = system.scan_environment(scenario.world, "shen_lan", 1)
    second_views = {view.id: view for view in second.full_observations}
    assert second_views["ebony_box"].observation_status == ScanObservationStatus.UNCHANGED


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
    assert "ebony_box" not in {view.id for view in projection.full_observations}
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
    item_ids = {
        view.id
        for view in [
            *context.items.observed_this_turn,
            *context.items.trusted_same_room,
            *context.items.other_memories,
        ]
    }
    assert "brass_key" not in item_ids
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
    assert "当前行动索引" not in first_prompt
    assert "可移动 Room id" not in first_prompt
    assert "【NPC】" in first_prompt
    assert "【Item】" in first_prompt
    assert scenario.world.entities["ebony_box"].description in first_prompt

    second_environment = system.scan_environment(scenario.world, "shen_lan", 1)
    second_context = ContextProjector().build(
        scenario.world, runtimes["shen_lan"], second_environment, 2
    )
    second_prompt = LLMAgent._render_context(second_context)
    assert "第 2 轮" not in second_prompt
    assert "【Item】" in second_prompt
    assert "当前可信的同房记忆" in second_prompt
    assert "ebony_box" in second_prompt
    assert scenario.world.entities["ebony_box"].description not in second_prompt


def test_context_groups_moved_trusted_and_other_memories_without_leaking_position(
    scenario, registry
):
    system, runtimes = make_system(scenario, registry, roll=0.1)
    runtime = runtimes["shen_lan"]
    first = system.scan_environment(scenario.world, "shen_lan", 0)
    ContextProjector().build(scenario.world, runtime, first, 0)

    # A remembered item can remain trusted even when this scan's random roll misses it.
    system.policy.rng = FixedRandom(0.99)
    unchanged = system.scan_environment(scenario.world, "shen_lan", 1)
    unchanged_context = ContextProjector().build(scenario.world, runtime, unchanged, 1)
    ring = next(
        view
        for view in unchanged_context.items.trusted_same_room
        if view.id == "silver_ring"
    )
    assert ring.interaction_status == InteractionStatus.CONTROLLED_BY_OTHER
    assert ring.controller_id == "qiao_man"

    # If an item moved but was not observed, only its remembered placement is exposed.
    moved_state = scenario.world.model_copy(deep=True)
    moved_state.placements["ebony_box"] = Placement(relation="inside", parent_id="study")
    moved_state.room_graph = RoomVisibilityGraph(edges={})
    moved_state.revision = 1
    moved_state = WorldState.model_validate(moved_state.model_dump(mode="python"))
    hidden_move = system.scan_environment(moved_state, "shen_lan", 2)
    hidden_context = ContextProjector().build(moved_state, runtime, hidden_move, 2)
    remembered = next(
        view for view in hidden_context.items.other_memories if view.id == "ebony_box"
    )
    assert remembered.placement.parent_id == "drawing_room"
    assert remembered.interaction_status == InteractionStatus.NOT_GUARANTEED

    # A full observation of the changed placement moves it into this-turn confirmation.
    visible_state = moved_state.model_copy(deep=True)
    visible_state.room_graph = RoomVisibilityGraph(edges={"drawing_room": {"study": 1.0}})
    visible_state.revision = 2
    visible_state = WorldState.model_validate(visible_state.model_dump(mode="python"))
    system.policy.rng = FixedRandom(0.1)
    observed_move = system.scan_environment(visible_state, "shen_lan", 3)
    moved_context = ContextProjector().build(visible_state, runtime, observed_move, 3)
    moved = next(
        view for view in moved_context.items.observed_this_turn if view.id == "ebony_box"
    )
    assert moved.observation_status == ScanObservationStatus.MOVED
    assert moved.placement.parent_id == "study"
