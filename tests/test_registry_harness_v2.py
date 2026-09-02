from __future__ import annotations

from typing import Literal

import pytest

from token_odyssey.inside_act.actions.contracts import (
    ActionEffect,
    ActionSpec,
    BaseActionIntent,
)
from token_odyssey.inside_act.actions.registry import ActionRegistry, RegistryError
from token_odyssey.inside_act.domain.events import (
    ActionEventData,
    AcceptedTurn,
    EventSource,
    RejectedTurn,
)
from token_odyssey.inside_act.domain.spatial import WorldState
from token_odyssey.inside_act.harness import WorldHarness
from token_odyssey.inside_act.mechanics import render_world_event


def plan(registry, actions, thought=""):
    return registry.parse_plan({"private_thought": thought, "actions": actions})


def test_builtin_registry_exposes_only_v3_action_fields(registry):
    catalog = registry.prompt_catalog()
    assert registry.kinds == (
        "say", "move", "search", "take", "give", "place", "show", "hide",
        "install", "operate", "wait",
    )
    assert "target_id" in catalog
    assert "target_ids" in catalog
    assert "target_entity_id" not in catalog
    assert "destination_room_id" not in catalog
    assert "amplitude" not in catalog
    assert catalog.startswith("1. say(")


def test_plan_uses_sequential_actions_and_defaults_amplitude(registry):
    parsed = plan(registry, [
        {"kind": "search", "target_id": "ebony_box"},
        {"kind": "take", "target_id": "brass_key"},
    ])
    assert [action.kind for action in parsed.actions] == ["search", "take"]
    assert [action.amplitude.value for action in parsed.actions] == ["normal", "normal"]
    dumped = parsed.model_dump(mode="json", serialize_as_any=True)
    assert dumped["actions"][0]["target_id"] == "ebony_box"
    assert "frames" not in dumped


def test_unknown_duplicate_and_old_action_fields_are_rejected(registry):
    with pytest.raises(RegistryError, match="未知 action"):
        registry.parse_command({"kind": "invented"})
    with pytest.raises(RegistryError, match="duplicate"):
        ActionRegistry([registry.spec("wait"), registry.spec("wait")])
    with pytest.raises(ValueError, match="不接受的字段"):
        registry.parse_command({"kind": "take", "target_entity_id": "x"})


def test_all_action_format_errors_are_returned_with_templates(registry):
    with pytest.raises(RegistryError) as caught:
        registry.parse_plan({
            "actions": [
                {"kind": "take"},
                {"kind": "say", "target_ids": "qiao_man", "content": "你好"},
                {"kind": "place", "item_id": "x", "target_id": "y", "extra": 1},
            ]
        })
    message = str(caught.value)
    assert "actions[0].target_id" in message
    assert "正确的 take 格式" in message
    assert "actions[1].target_ids" in message
    assert "正确的 say 格式" in message
    assert "actions[2].relation" in message
    assert "actions[2].extra" in message
    assert "正确的 place 格式" in message


def test_json_syntax_error_includes_location_and_plan_template(registry):
    with pytest.raises(RegistryError) as caught:
        registry.parse_plan('{"actions":[{"kind":"wait"}]')
    message = str(caught.value)
    assert "第 1 行" in message
    assert "正确格式示例" in message
    assert '"actions":[{"kind":"wait"}]' in message


def test_one_registered_action_parses_executes_and_renders(scenario):
    class PingIntent(BaseActionIntent):
        kind: Literal["ping"] = "ping"
        message: str

    class PingEventData(ActionEventData):
        message: str

    spec = ActionSpec(
        kind="ping", intent_model=PingIntent, validate=lambda _c, _i: [],
        event_model=PingEventData,
        plan=lambda _c, i: ActionEffect(data=PingEventData(message=i.message)),
        known_reference_extractor=lambda _i: set(), intrinsic_visibility=0.5,
        render_full=lambda _s, e: f"ping:{e.data.message}",
        render_partial=lambda _s, _e: "ping", prompt_usage="test action",
    )
    registry = ActionRegistry([spec])
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [{"kind": "ping", "message": "ok"}]), 1)
    assert isinstance(result, AcceptedTurn)
    assert registry.render(result.final_state, result.events[0], full=True) == "ping:ok"


def test_sequential_actions_use_draft_state_and_are_atomic(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["qiao_man"] = {"relation": "inside", "parent_id": "study"}
    harness = WorldHarness(WorldState.model_validate(raw), registry)
    result = harness.resolve("shen_lan", plan(registry, [
        {"kind": "move", "target_id": "study"},
        {"kind": "say", "target_ids": ["qiao_man"], "content": "到了"},
    ]), 1)
    assert isinstance(result, AcceptedTurn)
    assert [event.frame_index for event in result.events] == [0, 1]

    rejected = harness.resolve("shen_lan", plan(registry, [
        {"kind": "move", "target_id": "drawing_room"},
        {"kind": "move", "target_id": "missing"},
    ]), 2)
    assert isinstance(rejected, RejectedTurn)
    assert harness.state.root_room_of("shen_lan") == "study"


def test_search_knowledge_is_available_to_next_action(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [
            {"kind": "search", "target_id": "ebony_box"},
            {"kind": "take", "target_id": "brass_key"},
        ]),
        1,
        known_entity_ids={"ebony_box"},
    )
    assert isinstance(result, AcceptedTurn)
    assert harness.state.placements["brass_key"].parent_id == "shen_lan"


def test_five_actions_allowed_six_rejected_and_wait_exclusive(scenario, registry):
    five = [{"kind": "say", "target_ids": ["qiao_man"], "content": str(i)} for i in range(5)]
    result = WorldHarness(scenario.world, registry).resolve("shen_lan", plan(registry, five), 1)
    assert isinstance(result, AcceptedTurn)
    with pytest.raises(RegistryError, match="最多允许 5 项"):
        plan(registry, five + [five[0]])
    with pytest.raises(RegistryError, match="唯一"):
        plan(registry, [{"kind": "wait"}, five[0]])


def test_take_inside_reveals_and_take_attached_is_silent_without_notice(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    revealed = harness.resolve("qiao_man", plan(registry, [
        {"kind": "hide", "target_id": "silver_ring"},
        {"kind": "take", "target_id": "silver_ring"},
    ]), 1)
    assert isinstance(revealed, AcceptedTurn)
    assert harness.state.placements["silver_ring"].relation.value == "attached"

    duplicate = harness.resolve(
        "qiao_man", plan(registry, [{"kind": "take", "target_id": "silver_ring"}]), 2
    )
    assert isinstance(duplicate, AcceptedTurn)
    assert duplicate.events == ()
    assert duplicate.execution_notices == ()


def test_give_place_show_and_move_use_unified_fields(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    taken = harness.resolve("shen_lan", plan(registry, [{"kind": "take", "target_id": "ebony_box"}]), 1)
    assert isinstance(taken, AcceptedTurn)
    shown = harness.resolve("shen_lan", plan(registry, [{
        "kind": "show", "item_id": "ebony_box", "target_ids": ["qiao_man"]
    }]), 2)
    assert isinstance(shown, AcceptedTurn)
    given = harness.resolve("shen_lan", plan(registry, [{
        "kind": "give", "item_id": "ebony_box", "target_ids": ["qiao_man"]
    }]), 3)
    assert isinstance(given, AcceptedTurn)
    placed = harness.resolve("qiao_man", plan(registry, [{
        "kind": "place", "item_id": "ebony_box", "target_id": "drawing_room",
        "relation": "attached",
    }]), 4)
    assert isinstance(placed, AcceptedTurn)


def test_operate_without_mechanic_container_becomes_search(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [{"kind": "operate", "target_id": "ebony_box"}]),
        1,
        known_entity_ids={"ebony_box"},
    )
    assert isinstance(result, AcceptedTurn)
    assert [event.action_kind for event in result.events] == ["search"]
    assert result.observation_directives


def test_operate_without_mechanic_item_adds_world_no_effect(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "qiao_man",
        plan(registry, [{"kind": "operate", "target_id": "silver_ring"}]),
        1,
        known_entity_ids={"silver_ring"},
    )
    assert isinstance(result, AcceptedTurn)
    assert [event.source for event in result.events] == [EventSource.ACTION, EventSource.WORLD]
    assert result.events[1].mechanic_id is None
    assert "没有产生任何效果" in render_world_event(result.final_state, result.events[1], full=True)


def test_declared_operation_still_uses_scenario_mechanic(relay_scenario, registry):
    harness = WorldHarness(relay_scenario.world, registry)
    result = harness.resolve(
        "xia_mang",
        plan(registry, [{"kind": "operate", "target_id": "relay_console"}]),
        1,
        known_entity_ids={"relay_console"},
    )
    assert isinstance(result, AcceptedTurn)
    assert result.events[0].action_kind == "operate"
    assert result.events[1].mechanic_id is not None


def test_harness_snapshots_cannot_mutate_canonical_state(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan", plan(registry, [{"kind": "take", "target_id": "ebony_box"}]), 1
    )
    assert isinstance(result, AcceptedTurn)
    result.final_state.placements["ebony_box"].parent_id = "study"
    result.committed_frames[-1].after_state.placements["ebony_box"].parent_id = "foyer"
    assert harness.state.placements["ebony_box"].parent_id == "shen_lan"


def test_action_implementation_errors_propagate_without_mutating_world(scenario):
    class BrokenIntent(BaseActionIntent):
        kind: Literal["broken"] = "broken"

    def broken_validate(_context, _intent):
        raise RuntimeError("validator bug")

    spec = ActionSpec(
        kind="broken", intent_model=BrokenIntent, event_model=ActionEventData,
        validate=broken_validate,
        plan=lambda _context, _intent: ActionEffect(data=ActionEventData()),
        known_reference_extractor=lambda _intent: set(), intrinsic_visibility=0.0,
        render_full=lambda _state, _event: "broken",
        render_partial=lambda _state, _event: "broken", prompt_usage="broken",
    )
    registry = ActionRegistry([spec])
    harness = WorldHarness(scenario.world, registry)
    before = harness.state
    with pytest.raises(RuntimeError, match="validator bug"):
        harness.resolve("shen_lan", plan(registry, [{"kind": "broken"}]), 1)
    assert harness.state == before
    assert harness.world_log == []
