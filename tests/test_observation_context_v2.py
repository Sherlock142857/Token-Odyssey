from __future__ import annotations

import json

from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.inside_act.context import ContextProjector, ScanObservationStatus
from token_odyssey.inside_act.domain.events import AcceptedTurn, EventSource
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


def test_environment_projection_tracks_new_moved_and_unchanged(scenario, registry):
    system, _ = make_system(scenario, registry)
    first = system.scan_environment(scenario.world, "shen_lan", 0)
    first_views = {view.id: view for view in first.full_observations}
    assert first_views["ebony_box"].change == ScanObservationStatus.NEW
    assert "brass_key" not in first_views
    second = system.scan_environment(scenario.world, "shen_lan", 1)
    second_views = {view.id: view for view in second.full_observations}
    assert second_views["ebony_box"].change == ScanObservationStatus.UNCHANGED


def test_partial_environment_scan_does_not_reveal_or_remember_entity(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["entities"]["ebony_box"]["intrinsic_visibility"] = 0.5
    state = WorldState.model_validate(raw)
    system, runtimes = make_system(scenario, registry, roll=0.6)
    projection = system.scan_environment(state, "shen_lan", 1)
    assert "ebony_box" not in {view.id for view in projection.full_observations}
    assert "ebony_box" not in runtimes["shen_lan"].knowledge.entities
    partial_text = "\n".join(obs.text for obs in runtimes["shen_lan"].observations)
    assert state.item("ebony_box").name not in partial_text
    assert state.item("ebony_box").description not in partial_text


def test_search_result_is_kept_while_own_search_action_is_suppressed(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    system.scan_environment(scenario.world, "shen_lan", 0)
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        registry.parse_plan({"actions": [{"kind": "search", "target_id": "ebony_box"}]}),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    frame = result.committed_frames[0]
    projected = system.project_frame(frame)[result.events[0].sequence]
    assert projected["shen_lan"] is None
    system.apply_directives(frame.after_state, frame.directives, 1)
    assert "brass_key" in runtimes["shen_lan"].knowledge.entities
    assert any("黄铜钥匙" in obs.text for obs in runtimes["shen_lan"].observations)


def test_own_action_is_filtered_but_world_no_effect_remains(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    system.scan_environment(scenario.world, "qiao_man", 0)
    before = len(runtimes["qiao_man"].observations)
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "qiao_man",
        registry.parse_plan({"actions": [{"kind": "operate", "target_id": "silver_ring"}]}),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    assert [event.source for event in result.events] == [EventSource.ACTION, EventSource.WORLD]
    system.project_frame(result.committed_frames[0])
    added = runtimes["qiao_man"].observations[before:]
    assert len(added) == 1
    assert "没有产生任何效果" in added[0].text


def test_show_and_say_guarantee_targets_but_not_actor(scenario, registry):
    system, _ = make_system(scenario, registry, roll=0.99)
    harness = WorldHarness(scenario.world, registry)
    shown = harness.resolve("shen_lan", registry.parse_plan({"actions": [{
        "kind": "show", "item_id": "ebony_box", "target_ids": ["qiao_man"]
    }]}), 1)
    projection = system.project_frame(shown.committed_frames[0])[shown.events[0].sequence]
    assert projection["shen_lan"] is None
    assert projection["qiao_man"].level == ObservationLevel.FULL

    said = harness.resolve("shen_lan", registry.parse_plan({"actions": [{
        "kind": "say", "target_ids": ["qiao_man"], "content": "钥匙在盒子里"
    }]}), 2)
    projection = system.project_frame(said.committed_frames[0])[said.events[0].sequence]
    assert projection["shen_lan"] is None
    assert "钥匙" in projection["qiao_man"].text


def test_event_score_includes_visibility_and_amplitude_for_other_observers(scenario, registry):
    traces = []
    system, _ = make_system(
        scenario, registry, roll=0.99, trace=lambda category, payload: traces.append((category, payload))
    )
    result = WorldHarness(scenario.world, registry).resolve(
        "qiao_man",
        registry.parse_plan({"actions": [{
            "kind": "hide", "amplitude": "subtle", "target_id": "silver_ring"
        }]}),
        1,
    )
    system.project_frame(result.committed_frames[0])
    external = [
        payload for category, payload in traces
        if category == "event_projection" and payload["observer_id"] == "shen_lan"
    ][-1]
    assert external["score"] == 0.12


def test_context_is_stable_json_without_redundant_fields(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    environment = system.scan_environment(scenario.world, "shen_lan", 0)
    context = ContextProjector().build(scenario.world, runtimes["shen_lan"], environment, 1)
    rendered = LLMAgent._render_context(context)
    payload = json.loads(rendered)
    assert set(payload) == {
        "actor_id", "location", "observations_since_last_action",
        "last_action_feedback", "characters", "items", "inventory",
    }
    assert payload["location"] == {"id": "drawing_room", "name": "会客厅"}
    assert set(payload["items"]) == {"new_or_changed", "visible_same_location", "memories"}
    assert isinstance(payload["items"]["new_or_changed"][0]["placement"], dict)
    for forbidden in (
        "last_observed_round", "interaction_status", "controller_id", "kind",
        "World Log", "Harness", "【", "】",
    ):
        assert forbidden not in rendered


def test_stable_visible_entities_do_not_repeat_description(scenario, registry):
    system, runtimes = make_system(scenario, registry)
    first = system.scan_environment(scenario.world, "shen_lan", 0)
    first_context = ContextProjector().build(scenario.world, runtimes["shen_lan"], first, 0)
    assert scenario.world.item("ebony_box").description in LLMAgent._render_context(first_context)

    second = system.scan_environment(scenario.world, "shen_lan", 1)
    second_context = ContextProjector().build(scenario.world, runtimes["shen_lan"], second, 1)
    rendered = LLMAgent._render_context(second_context)
    assert "ebony_box" in rendered
    assert scenario.world.item("ebony_box").description not in rendered


def test_context_keeps_remembered_placement_without_leaking_current_position(scenario, registry):
    system, runtimes = make_system(scenario, registry, roll=0.1)
    runtime = runtimes["shen_lan"]
    first = system.scan_environment(scenario.world, "shen_lan", 0)
    ContextProjector().build(scenario.world, runtime, first, 0)

    moved_state = scenario.world.model_copy(deep=True)
    moved_state.placements["ebony_box"] = Placement(relation="inside", parent_id="study")
    moved_state.room_graph = RoomVisibilityGraph(edges={})
    moved_state.revision = 1
    moved_state = WorldState.model_validate(moved_state.model_dump(mode="python"))
    system.policy.rng = FixedRandom(0.99)
    hidden = system.scan_environment(moved_state, "shen_lan", 1)
    context = ContextProjector().build(moved_state, runtime, hidden, 1)
    remembered = next(view for view in context.items.memories if view.id == "ebony_box")
    assert remembered.placement.parent_id == "drawing_room"


def test_inventory_separates_direct_attached_and_inside_only(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["ebony_box"] = {"relation": "attached", "parent_id": "shen_lan"}
    raw["placements"]["guest_ledger"] = {"relation": "inside", "parent_id": "shen_lan"}
    state = WorldState.model_validate(raw)
    system, runtimes = make_system(scenario, registry)
    environment = system.scan_environment(state, "shen_lan", 0)
    context = ContextProjector().build(state, runtimes["shen_lan"], environment, 0)
    assert {view.id for view in context.inventory.attached} == {"ebony_box"}
    assert {view.id for view in context.inventory.inside} == {"guest_ledger"}
    assert "brass_key" not in {
        view.id for view in [*context.inventory.attached, *context.inventory.inside]
    }
    assert "ebony_box" not in {
        view.id
        for view in [
            *context.items.new_or_changed,
            *context.items.visible_same_location,
            *context.items.memories,
        ]
    }
